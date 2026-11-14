"""Async native schedule transport; no browser cookies or automatic write retries."""

import asyncio
import base64
import time
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import aiohttp
import gpsoauth
import requests

from .const import NETWORK_TIMEOUT, SYNC_URL, TASKS_APP, TASKS_CERT, TASKS_SCOPE
from .exceptions import NativeAuthError, OutcomeUnknown, TasksError, WrongAccount
from .protocol import (
    ProtocolError,
    Schedule,
    Snapshot,
    build_schedule_mutation,
    decode_sync_response,
    make_schedule,
    sync_read_body,
)


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
        if not snapshot.list_ids.intersection(private_id(v) for v in public_lists):
            raise WrongAccount("Could not match native credentials to the OAuth account.")

    async def read(self):
        return await self._post(sync_read_body())

    @staticmethod
    def find(snapshot, task_id, list_id):
        record = snapshot.tasks.get(private_id(task_id))
        if record is None or record.list_id != private_id(list_id):
            raise TasksError("Task is not yet visible in native Tasks; refresh the list.")
        return record

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
