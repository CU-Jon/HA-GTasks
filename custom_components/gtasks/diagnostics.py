"""Diagnostics without credentials, account identifiers or task content."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    data = coordinator.data or {"lists": [], "tasks": {}}
    return {
        "version": "0.1.2",
        "last_update_success": coordinator.last_update_success,
        "list_count": len(data["lists"]),
        "task_count": sum(len(tasks) for tasks in data["tasks"].values()),
        "native_schedules": True,
    }
