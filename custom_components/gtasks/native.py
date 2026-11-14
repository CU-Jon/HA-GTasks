"""Async native schedule transport; no browser cookies or automatic write retries."""

import asyncio
import base64
import logging
import time
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import aiohttp
import gpsoauth
import requests

from .const import NETWORK_TIMEOUT, SYNC_URL, TASKS_APP, TASKS_CERT, TASKS_SCOPE
from .exceptions import (
    AccountVerificationError,
    NativeAuthError,
    NativeTaskUnavailable,
    OutcomeUnknown,
    TasksError,
)
from .protocol import (
    ProtocolError,
    Schedule,
    Snapshot,
    build_schedule_mutation,
    decode_sync_response,
    make_schedule,
    sync_read_body,
)

_LOGGER = logging.getLogger(__name__)


def public_id(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def private_id(value: str) -> str:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        result = decoded.decode()
        if not result or public_id(result) != value.rstrip("="):
            raise ValueError
        return result
    except ValueError, UnicodeError:
        raise TasksError("Invalid Google Tasks identifier.") from None


def exchange_token(email: str, master_token: str, device_id: str) -> tuple[str, int]:
    """Blocking Android token exchange. Caller must use the HA executor."""
    try:
        with requests.Session() as session:
            session.mount(gpsoauth.AUTH_URL, gpsoauth.AuthHTTPAdapter())
            session.headers.update(gpsoauth.build_auth_headers())
            response = session.post(
                gpsoauth.AUTH_URL,
                data=gpsoauth.build_oauth_data(
                    email,
                    master_token,
                    device_id,
                    service=TASKS_SCOPE,
                    app=TASKS_APP,
                    client_sig=TASKS_CERT,
                    sdk_version=24,
                ),
                timeout=NETWORK_TIMEOUT,
                allow_redirects=False,
            )
            if response.status_code in (400, 401, 403):
                raise NativeAuthError("Native Google Tasks authorization was rejected.")
            if response.status_code != 200:
                raise TasksError("Could not contact Google's native authorization service.")
            result = gpsoauth.google.parse_auth_response(response.text)
        token = result["Auth"]
        if not isinstance(token, str) or not token:
            raise ValueError
        lifetime = min(
            1800, max(0, int(result.get("Expiry", time.time() + 1860)) - int(time.time()) - 60)
        )
        return token, lifetime
    except TasksError:
        raise
    except requests.RequestException, TimeoutError:
        raise TasksError("Could not contact Google's native authorization service.") from None
    except KeyError, ValueError, TypeError:
        raise NativeAuthError("Native Google Tasks authorization was rejected.") from None


def obtain_master_token(email: str, temporary_token: str, device_id: str) -> str:
    """Exchange a user-provided browser token in the executor; never retain it."""
    try:
        with requests.Session() as session:
            session.mount(gpsoauth.AUTH_URL, gpsoauth.AuthHTTPAdapter())
            session.headers.update(gpsoauth.build_auth_headers())
            response = session.post(
                gpsoauth.AUTH_URL,
                data=gpsoauth.build_exchange_token_data(email, temporary_token, device_id),
                timeout=NETWORK_TIMEOUT,
                allow_redirects=False,
            )
            if response.status_code in (400, 401, 403):
                raise NativeAuthError("Google rejected the temporary sign-in token.")
            if response.status_code != 200:
                raise TasksError("Could not contact Google's native authorization service.")
            token = gpsoauth.google.parse_auth_response(response.text).get("Token")
        if not isinstance(token, str) or not token:
            raise NativeAuthError("Google rejected the temporary sign-in token.")
        return token
    except requests.RequestException, TimeoutError:
        raise TasksError("Could not contact Google's native authorization service.") from None
    except ValueError, TypeError, KeyError:
        raise NativeAuthError("Google rejected the temporary sign-in token.") from None


def schedule_for_due(due: date | datetime | None, timezone: str) -> Schedule | None:
    if due is None:
        return None
    if not isinstance(due, datetime):
        return make_schedule(due.isoformat(), None, timezone)
    if due.tzinfo is None or due.utcoffset() is None:
        if due.second or due.microsecond:
            raise TasksError(
                "Google Tasks schedules support minute precision; seconds must be zero."
            )
        return make_schedule(due.date().isoformat(), due.strftime("%H:%M"), timezone)
    if due.second or due.microsecond:
        raise TasksError("Google Tasks schedules support minute precision; seconds must be zero.")
    # An explicit offset resolves a repeated DST hour without guessing.
    local = due.astimezone(ZoneInfo(timezone))
    return Schedule(local.date(), local.time().replace(tzinfo=None), timezone, int(due.timestamp()))


def due_for_record(record) -> date | datetime | None:
    schedule = record.schedule
    if schedule is None:
        return record.due
    if schedule.clock is None:
        return schedule.due
    return datetime.fromtimestamp(schedule.epoch_seconds, UTC).astimezone(
        ZoneInfo(schedule.timezone)
    )


class NativeClient:
    def __init__(self, hass, session, email, master_token, device_id):
        self.hass = hass
        self.session = session
        self._email = email
        self._master = master_token
        self._device = device_id
        self._token = None
        self._expires = 0.0
        self._blocked_until = 0.0
        self._list_aliases = {}

    async def _access_token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        token, lifetime = await self.hass.async_add_executor_job(
            exchange_token, self._email, self._master, self._device
        )
        self._token, self._expires = token, time.monotonic() + lifetime
        return token

    async def _post(self, body, *, write=False):
        if time.monotonic() < self._blocked_until:
            raise TasksError("Google Tasks is rate limited; wait before refreshing.")
        token = await self._access_token()
        try:
            async with self.session.post(
                SYNC_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json+protobuf",
                },
                timeout=aiohttp.ClientTimeout(total=NETWORK_TIMEOUT),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    self._token = None
                    raise NativeAuthError("Native Google Tasks authorization expired.")
                if response.status == 429:
                    retry = response.headers.get("Retry-After", "60")
                    self._blocked_until = time.monotonic() + max(
                        60, int(retry) if retry.isdigit() else 60
                    )
                    raise TasksError("Google Tasks is rate limited; wait before refreshing.")
                if response.status == 403:
                    raise TasksError("Google refused access to native Tasks.")
                if write and response.status >= 500:
                    raise OutcomeUnknown()
                if response.status != 200:
                    raise TasksError("Google rejected the native Tasks request.")
                if write:
                    return None
                raw = await response.text()
        except aiohttp.ClientError, TimeoutError:
            if write:
                raise OutcomeUnknown() from None
            raise TasksError("Could not read native Google Tasks.") from None
        try:
            return decode_sync_response(raw)
        except ProtocolError:
            raise TasksError("Native Tasks response format is not recognized.") from None

    def validate_account(self, snapshot: Snapshot, public_lists=()):
        # Match opaque list IDs independently obtained from OAuth. Do not infer
        # account identity from undocumented fields on a task record.
        candidates = set()
        for value in public_lists:
            candidates.add(value)
            try:
                candidates.add(private_id(value))
            except TasksError:
                pass  # Public identifiers are opaque, not guaranteed to be base64.
        if not snapshot.list_ids.intersection(candidates):
            raise AccountVerificationError(
                "Private list identifiers could not establish account identity."
            )

    async def check_account(self, snapshot: Snapshot, public_lists, public_tasks=None):
        """Bind list identifiers using independent OAuth task membership evidence."""
        expected = set(public_lists)
        aliases = {}
        for list_id in expected:
            candidates = {list_id}
            try:
                candidates.add(private_id(list_id))
            except TasksError:
                pass
            matches = snapshot.list_ids.intersection(candidates)
            if len(matches) == 1:
                aliases[list_id] = next(iter(matches))
        for list_id, tasks in (public_tasks or {}).items():
            if list_id not in expected:
                raise AccountVerificationError("Unexpected public task list in verification data.")
            for task_id in tasks:
                candidates = {task_id}
                try:
                    candidates.add(private_id(task_id))
                except TasksError:
                    pass
                records = [snapshot.tasks[key] for key in candidates if key in snapshot.tasks]
                if len(records) > 1:
                    raise AccountVerificationError("Ambiguous native task identifier.")
                if not records or not records[0].list_id:
                    continue
                native_list = records[0].list_id
                if list_id in aliases and aliases[list_id] != native_list:
                    raise AccountVerificationError("Conflicting native task-list membership.")
                aliases[list_id] = native_list
        if not aliases:
            _LOGGER.debug(
                "Account verification needs OAuth task evidence: oauth_lists=%d "
                "oauth_tasks=%d native_lists=%d native_tasks=%d",
                len(expected),
                sum(len(ids) for ids in (public_tasks or {}).values()),
                len(snapshot.list_ids),
                len(snapshot.tasks),
            )
            raise AccountVerificationError(
                "Could not match tasks read through OAuth to native Tasks."
            )
        if len(set(aliases.values())) != len(aliases):
            raise AccountVerificationError("Ambiguous native task-list membership.")
        self._list_aliases = aliases

    async def read(self):
        return await self._post(sync_read_body())

    def find(self, snapshot, task_id, list_id):
        task_ids, list_ids = {task_id}, {list_id}
        if list_id in self._list_aliases:
            list_ids.add(self._list_aliases[list_id])
        for value, candidates in ((task_id, task_ids), (list_id, list_ids)):
            try:
                candidates.add(private_id(value))
            except TasksError:
                pass
        records = [snapshot.tasks[key] for key in task_ids if key in snapshot.tasks]
        if not records:
            raise NativeTaskUnavailable("missing_task")
        if len(records) != 1:
            raise NativeTaskUnavailable("ambiguous_task")
        if records[0].list_id not in list_ids:
            raise NativeTaskUnavailable("list_mismatch")
        return records[0]

    async def set_schedule(self, list_id, task_id, desired):
        snapshot = await self.read()
        before = self.find(snapshot, task_id, list_id)
        if before.recurring:
            raise TasksError("Changing a recurring task's schedule is not supported.")
        try:
            body = build_schedule_mutation(snapshot, before.task_id, desired)
        except ProtocolError:
            raise TasksError(
                "A fresh task version is required before changing its schedule."
            ) from None
        await self._post(body, write=True)
        try:
            await asyncio.sleep(1)
            after = self.find(await self.read(), task_id, list_id)
            if after.schedule != desired or after.due != (desired.due if desired else None):
                raise OutcomeUnknown()
            if (before.title, before.notes, before.completed, before.list_id) != (
                after.title,
                after.notes,
                after.completed,
                after.list_id,
            ):
                raise OutcomeUnknown()
        except Exception:
            raise OutcomeUnknown() from None
