"""Diagnostics without credentials, account identifiers or task content."""

from homeassistant.loader import async_get_integration

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    data = coordinator.data or {"lists": [], "tasks": {}}
    integration = await async_get_integration(hass, DOMAIN)
    return {
        "version": str(integration.version),
        "last_update_success": coordinator.last_update_success,
        "list_count": len(data["lists"]),
        "task_count": sum(len(tasks) for tasks in data["tasks"].values()),
        "native_schedules": True,
        "completed_tasks_hidden": sum(data.get("completed_tasks_hidden", {}).values()),
        "completed_tasks_public_only": sum(
            item.get("status") == "completed" for tasks in data["tasks"].values() for item in tasks
        ),
        "tasks_with_unverified_times": sum(
            item.get("_native_schedule_available") is False
            for tasks in data["tasks"].values()
            for item in tasks
        ),
    }
