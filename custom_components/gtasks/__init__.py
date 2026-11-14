"""Google Tasks with Times, adapted from the Home Assistant integration."""

from homeassistant.const import CONF_EMAIL, Platform
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TasksApi
from .const import CONF_DEVICE_ID, CONF_MASTER_TOKEN
from .coordinator import GoogleTasksConfigEntry, TaskUpdateCoordinator
from .native import NativeClient

PLATFORMS = [Platform.TODO]


async def async_setup_entry(hass, entry: GoogleTasksConfigEntry) -> None:
    """Connect both transports to the same account before adding entities."""
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    oauth = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    session = async_get_clientsession(hass)
    native = NativeClient(
        hass,
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_MASTER_TOKEN],
        entry.data[CONF_DEVICE_ID],
    )
    api = TasksApi(session, oauth, native, hass.config.time_zone)
    coordinator = TaskUpdateCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass, entry: GoogleTasksConfigEntry) -> None:
    """Unload entities and their listeners."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
