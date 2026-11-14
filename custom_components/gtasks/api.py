"""Google Tasks public CRUD plus verified native schedules, bound to HA OAuth.

Adapted from Home Assistant's Google Tasks integration (Apache-2.0). Public REST
uses HA's shared async session rather than blocking discovery clients.
"""

import asyncio
import logging
from collections import Counter
from datetime import date, datetime
from urllib.parse import quote

import aiohttp
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import NETWORK_TIMEOUT, TASKS_URL
from .exceptions import NativeTaskUnavailable, OutcomeUnknown, PartialChange, TasksError
from .native import due_for_record, schedule_for_due
from .protocol import ProtocolError

_LOGGER = logging.getLogger(__name__)


class TasksApi:
    def __init__(self, session, oauth, native, timezone):
        self.session, self.oauth, self.native, self.timezone = session, oauth, native, timezone
        self.lock = asyncio.Lock()

    @staticmethod
    def _public_due(item):
        try:
            return datetime.fromisoformat(item["due"]).date() if item.get("due") else None
        except ValueError, TypeError:
            raise TasksError("Google returned an invalid task due date.") from None

    async def _request(self, method, path, *, body=None, params=None):
        write = method != "GET"
        await self.oauth.async_ensure_token_valid()
        try:
            async with self.session.request(
                method,
                TASKS_URL + path,
                json=body,
                params={k: v for k, v in (params or {}).items() if v is not None},
                headers={"Authorization": f"Bearer {self.oauth.token[CONF_ACCESS_TOKEN]}"},
                timeout=aiohttp.ClientTimeout(total=NETWORK_TIMEOUT),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise ConfigEntryAuthFailed("Google Tasks OAuth authorization expired.")
                if write and response.status >= 500:
                    raise OutcomeUnknown()
                if response.status == 404:
                    raise TasksError("Task or task list no longer exists.")
                if response.status == 429:
                    raise TasksError("Google rate limited Tasks; wait before refreshing.")
                if not 200 <= response.status < 300:
                    raise TasksError("Google rejected the Tasks request.")
                if response.status == 204:
                    return {}
                data = await response.json()
                if not isinstance(data, dict) or "error" in data:
                    if write:
                        raise OutcomeUnknown()
                    raise TasksError("Google Tasks returned an unexpected response.")
                return data
        except aiohttp.ClientError, TimeoutError, ValueError:
            if write:
                raise OutcomeUnknown() from None
            raise TasksError("Could not read Google Tasks.") from None

    async def _pages(self, path, params=None):
        items, token, seen = [], None, set()
        while True:
            data = await self._request(
                "GET", path, params={**(params or {}), "maxResults": 100, "pageToken": token}
            )
            items.extend(data.get("items", []))
            token = data.get("nextPageToken")
            if not token:
                return items
            if token in seen:
                raise TasksError("Google Tasks repeated a pagination token.")
            seen.add(token)

    async def refresh(self):
        async with self.lock:
            lists = await self._pages("/users/@me/lists")
            tasks = {}
            public_membership = {}
            skipped_subtasks = 0
            for item in lists:
                tasks[item["id"]] = await self._pages(
                    f"/lists/{quote(item['id'], safe='')}/tasks",
                    {"showCompleted": "true", "showHidden": "true", "showDeleted": "false"},
                )
                public_membership[item["id"]] = [task["id"] for task in tasks[item["id"]]]
                skipped_subtasks += sum(
                    task.get("parent") is not None for task in tasks[item["id"]]
                )
                tasks[item["id"]] = [
                    task for task in tasks[item["id"]] if task.get("parent") is None
                ]
            snapshot = None
            if any(
                task.get("status") != "completed" for records in tasks.values() for task in records
            ):
                snapshot = await self.native.read()
                await self.native.check_account(
                    snapshot,
                    (item["id"] for item in lists),
                    public_membership,
                )
            missing = Counter()
            statuses = Counter()
            for list_id, records in tasks.items():
                for item in records:
                    if item.get("status") == "completed":
                        item["_native_due"] = self._public_due(item)
                        # Intentionally not queried, rather than an unverified active time.
                        item["_native_schedule_available"] = None
                        continue
                    try:
                        native = self.native.find(snapshot, item["id"], list_id)
                        item["_native_due"] = due_for_record(native)
                        item["_native_schedule_available"] = True
                    except (NativeTaskUnavailable, ProtocolError) as err:
                        reason = (
                            err.reason
                            if isinstance(err, NativeTaskUnavailable)
                            else "invalid_schedule"
                        )
                        missing[reason] += 1
                        statuses[
                            "completed" if item.get("status") == "completed" else "active"
                        ] += 1
                        item["_native_due"] = self._public_due(item)
                        item["_native_schedule_available"] = False
            if missing:
                _LOGGER.warning(
                    "Native schedule coverage incomplete: public_tasks=%d native_tasks=%d "
                    "missing_task=%d list_mismatch=%d ambiguous_task=%d invalid_schedule=%d "
                    "unverified_active=%d unverified_completed=%d skipped_subtasks=%d. "
                    "Unverified active tasks show public dates only; schedule edits are blocked.",
                    sum(len(items) for items in tasks.values()),
                    len(snapshot.tasks),
                    missing["missing_task"],
                    missing["list_mismatch"],
                    missing["ambiguous_task"],
                    missing["invalid_schedule"],
                    statuses["active"],
                    statuses["completed"],
                    skipped_subtasks,
                )
            return lists, tasks

    def _desired(self, due):
        try:
            return schedule_for_due(due, self.timezone)
        except ProtocolError, ValueError, KeyError:
            raise TasksError(
                "Invalid or ambiguous due time; specify an explicit UTC offset for DST."
            ) from None

    async def insert(self, task_list_id, task, due: date | datetime | None):
        desired = self._desired(due)
        body = {k: v for k, v in task.items() if k != "due"}
        if desired is not None:
            body["due"] = desired.due.isoformat() + "T00:00:00Z"
        async with self.lock:
            result = await self._request(
                "POST", f"/lists/{quote(task_list_id, safe='')}/tasks", body=body
            )
            if desired is not None and desired.clock is not None:
                try:
                    await self.native.set_schedule(task_list_id, result["id"], desired)
                except Exception:
                    raise PartialChange(
                        "The task was created, but its time was not confirmed. "
                        "Refresh before creating another."
                    ) from None

    async def patch(self, task_list_id, task_id, task, due, *, schedule_changed):
        async with self.lock:
            body = {k: v for k, v in task.items() if k != "due"}
            path = f"/lists/{quote(task_list_id, safe='')}/tasks/{quote(task_id, safe='')}"
            if body.get("status") == "needsAction":
                body["completed"] = None
            if not schedule_changed:
                # HA supplied the previous displayed due value. Content/status-only
                # updates never rewrite due, including when a completed task vanishes
                # from Sync or its hidden time cannot be read.
                await self._request("PATCH", path, body=body)
                return
            desired = self._desired(due)
            current = self.native.find(await self.native.read(), task_id, task_list_id)
            if current.completed:
                raise TasksError("Reopen the task before changing its schedule.")
            # Completion/content edits must not rewrite another client's timezone.
            try:
                unchanged = due_for_record(current) == due
            except ProtocolError:
                raise TasksError("The task's current schedule could not be decoded.") from None
            if not unchanged and current.recurring:
                raise TasksError("Changing a recurring task's schedule is not supported.")
            if not unchanged and body.get("status") == "completed":
                # Completion can remove the record from Sync. Set and verify its
                # schedule while it is still active, then complete through REST.
                await self.native.set_schedule(task_list_id, task_id, desired)
                try:
                    await self._request("PATCH", path, body=body)
                except Exception:
                    raise PartialChange(
                        "The schedule was updated, but completion was not confirmed. "
                        "Refresh before retrying."
                    ) from None
                return
            await self._request(
                "PATCH",
                f"/lists/{quote(task_list_id, safe='')}/tasks/{quote(task_id, safe='')}",
                body=body,
            )
            if not unchanged:
                try:
                    await self.native.set_schedule(task_list_id, task_id, desired)
                except Exception:
                    raise PartialChange(
                        "Task details were updated, but the schedule was not confirmed. "
                        "Refresh before retrying."
                    ) from None

    async def delete(self, task_list_id, task_ids):
        async with self.lock:
            for task_id in task_ids:
                await self._request(
                    "DELETE",
                    f"/lists/{quote(task_list_id, safe='')}/tasks/{quote(task_id, safe='')}",
                )

    async def move(self, task_list_id, task_id, previous=None):
        async with self.lock:
            await self._request(
                "POST",
                f"/lists/{quote(task_list_id, safe='')}/tasks/{quote(task_id, safe='')}/move",
                params={"previous": previous},
            )
