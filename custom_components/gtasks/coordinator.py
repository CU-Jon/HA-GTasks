"""One refresh and one serialized writer for each Google account."""

import logging
from datetime import UTC, datetime, timedelta
from heapq import nlargest

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_COMPLETED_TASK_LIMIT, DEFAULT_COMPLETED_TASK_LIMIT
from .exceptions import NativeAuthError, TasksError, WrongAccount

type GoogleTasksConfigEntry = ConfigEntry[TaskUpdateCoordinator]


def _completion_time(task):
    """Compare completion instants consistently, placing missing timestamps last."""
    try:
        value = datetime.fromisoformat(task.get("completed", ""))
    except ValueError, TypeError:
        return datetime.min.replace(tzinfo=UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _limit_completed(tasks, limit):
    """Keep all active items and the newest completed items, regardless of API order."""
    active = [task for task in tasks if task.get("status") != "completed"]
    completed = [task for task in tasks if task.get("status") == "completed"]
    return active + nlargest(limit, completed, key=_completion_time), max(0, len(completed) - limit)


class TaskUpdateCoordinator(DataUpdateCoordinator):
    """Share an account snapshot across all its to-do lists."""

    def __init__(self, hass, entry, api):
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name="Google Tasks with Times",
            config_entry=entry,
            update_interval=timedelta(minutes=30),
        )
        self.api = api

    async def _async_update_data(self):
        try:
            lists, tasks = await self.api.refresh()
        except (NativeAuthError, WrongAccount) as err:
            raise ConfigEntryAuthFailed(str(err)) from None
        except TasksError as err:
            raise UpdateFailed(str(err)) from None
        limit = self.config_entry.options.get(
            CONF_COMPLETED_TASK_LIMIT, DEFAULT_COMPLETED_TASK_LIMIT
        )
        visible, hidden = {}, {}
        for list_id, records in tasks.items():
            visible[list_id], hidden[list_id] = _limit_completed(records, limit)
        return {"lists": lists, "tasks": visible, "completed_tasks_hidden": hidden}
