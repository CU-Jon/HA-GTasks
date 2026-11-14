"""Large completed histories stay bounded without losing active tasks."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import probatio
import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.gtasks.coordinator import _limit_completed
from custom_components.gtasks.diagnostics import async_get_config_entry_diagnostics

from .conftest import LIST_ID, TASK_ID


def completed_history(count):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    return [
        {
            "id": f"completed-{i}",
            "title": f"Completed {i}",
            "status": "completed",
            "completed": (start + timedelta(minutes=i)).isoformat(),
            # Position deliberately opposes completion order.
            "position": str(count - i).zfill(6),
            "_native_due": None,
        }
        for i in range(count)
    ]


def test_completion_order_uses_instants_not_string_or_position():
    records = [
        {"id": "older", "status": "completed", "completed": "2020-01-01T13:00:00+02:00"},
        {"id": "newer", "status": "completed", "completed": "2020-01-01T12:00:00Z"},
        {"id": "unknown", "status": "completed"},
        {"id": "active"},
    ]
    visible, hidden = _limit_completed(records, 1)
    assert [task["id"] for task in visible] == ["active", "newer"]
    assert hidden == 2
    assert len(records) == 4


async def test_large_history_limits_services_per_list_but_keeps_all_active(
    hass, data, setup_integration, entry
):
    active = [dict(data["tasks"][LIST_ID][0], id=f"active-{i}") for i in range(150)]
    data["tasks"][LIST_ID] = completed_history(5500) + active
    data["lists"].append({"id": "other-list", "title": "Other list"})
    data["tasks"]["other-list"] = completed_history(140)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    result = await hass.services.async_call(
        "todo", "get_items", {"entity_id": "todo.my_tasks"}, blocking=True, return_response=True
    )
    items = result["todo.my_tasks"]["items"]
    assert len(items) == 250
    assert {task["uid"] for task in items if task["status"] == "needs_action"} == {
        task["id"] for task in active
    }
    assert {task["uid"] for task in items if task["status"] == "completed"} == {
        f"completed-{i}" for i in range(5400, 5500)
    }
    assert hass.states.get("todo.my_tasks").state == "150"
    assert hass.states.get("todo.my_tasks").attributes["completed_tasks_hidden"] == 5400
    assert len(entry.runtime_data.data["tasks"]["other-list"]) == 100
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["completed_tasks_hidden"] == 5440
    assert diagnostics["task_count"] == 350
    assert len(data["tasks"][LIST_ID]) == 5650
    setup_integration.delete.assert_not_awaited()


async def test_cached_items_are_reused_and_refresh_invalidates(
    hass, data, setup_integration, entry
):
    entity = hass.data["todo"].get_entity("todo.my_tasks")
    before = entity.todo_items
    with patch("custom_components.gtasks.todo._convert_api_item") as convert:
        assert entity.todo_items is before
        assert entity.todo_items is before
        convert.assert_not_called()
    data["tasks"][LIST_ID][0]["title"] = "Updated title"
    await entry.runtime_data.async_refresh()
    assert entity.todo_items is not before
    assert entity.todo_items[0].summary == "Updated title"


@pytest.mark.parametrize("limit", [0, 2, 500])
async def test_options_save_reload_and_apply_without_changing_credentials(
    hass, data, setup_integration, entry, limit
):
    original_credentials = dict(entry.data)
    data["tasks"][LIST_ID].extend(completed_history(6))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {"completed_task_limit": 100}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"completed_task_limit": limit}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {"completed_task_limit": limit}
    assert entry.data == original_credentials
    visible = entry.runtime_data.data["tasks"][LIST_ID]
    assert len(visible) == 1 + min(6, limit)
    assert visible[0]["id"] == TASK_ID
    assert entry.runtime_data.data["completed_tasks_hidden"][LIST_ID] == max(0, 6 - limit)
    setup_integration.delete.assert_not_awaited()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["data_schema"]({}) == {"completed_task_limit": limit}


@pytest.mark.parametrize("invalid", [-1, 501, 1.5, "all"])
async def test_options_reject_invalid_limits(hass, entry, invalid):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(probatio.Invalid):
        result["data_schema"]({"completed_task_limit": invalid})
