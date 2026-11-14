"""One refresh and one serialized writer for each Google account."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .exceptions import NativeAuthError, TasksError, WrongAccount

type GoogleTasksConfigEntry = ConfigEntry[TaskUpdateCoordinator]


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
        return {"lists": lists, "tasks": tasks}
