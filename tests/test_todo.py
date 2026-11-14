"""Exercise real HA To-do services, including partial updates and time support."""

from datetime import date

import pytest
from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.exceptions import HomeAssistantError

from custom_components.gtasks.diagnostics import async_get_config_entry_diagnostics
from custom_components.gtasks.exceptions import PartialChange

from .conftest import DUE, LIST_ID, TASK_ID


async def test_entity_and_completion_preserve_time(hass, setup_integration):
    state = hass.states.get("todo.my_tasks")
    assert state is not None
    assert state.attributes["friendly_name"] == "My TASKS"
    assert state.attributes["supported_features"] & TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    result = await hass.services.async_call(
        "todo", "get_items", {"entity_id": state.entity_id}, blocking=True, return_response=True
    )
    assert result[state.entity_id]["items"][0]["due"] == DUE.isoformat()
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": state.entity_id, "item": TASK_ID, "status": "completed"},
        blocking=True,
    )
    args = setup_integration.patch.call_args
    assert args.args == (LIST_ID, TASK_ID)
    assert args.kwargs["due"] == DUE
    assert args.kwargs["task"] == {
        "title": "Test task",
        "status": "completed",
        "notes": "Description",
    }


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("due_datetime", "2026-11-16T00:00:00-05:00", "datetime"),
        ("due_date", "2026-11-16", date(2026, 11, 16)),
        ("due_datetime", None, None),
    ],
)
async def test_update_due(hass, setup_integration, field, value, expected):
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": "todo.my_tasks", "item": TASK_ID, field: value},
        blocking=True,
    )
    due = setup_integration.patch.call_args.kwargs["due"]
    if expected == "datetime":
        assert due.timestamp() == 1794805200
    else:
        assert due == expected


async def test_create_with_time(hass, setup_integration):
    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": "todo.my_tasks", "item": "Timed task", "due_datetime": DUE.isoformat()},
        blocking=True,
    )
    assert setup_integration.insert.call_args.kwargs["due"] == DUE


async def test_delete_and_move(hass, setup_integration, entry):
    entity = hass.data["todo"].get_entity("todo.my_tasks")
    await entity.async_move_todo_item(TASK_ID)
    setup_integration.move.assert_awaited_once_with(LIST_ID, TASK_ID, previous=None)
    await hass.services.async_call(
        "todo", "remove_item", {"entity_id": "todo.my_tasks", "item": TASK_ID}, blocking=True
    )
    setup_integration.delete.assert_awaited_once_with(LIST_ID, [TASK_ID])


async def test_partial_failure_refreshes(hass, setup_integration):
    setup_integration.insert.side_effect = PartialChange("Task created; time unconfirmed.")
    before = setup_integration.refresh.await_count
    with pytest.raises(HomeAssistantError, match="Task created"):
        await hass.services.async_call(
            "todo", "add_item", {"entity_id": "todo.my_tasks", "item": "Test"}, blocking=True
        )
    assert setup_integration.refresh.await_count > before


async def test_new_and_removed_lists(hass, setup_integration, entry, data):
    data["lists"].append({"id": "new-list", "title": "New list"})
    data["tasks"]["new-list"] = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("todo.new_list") is not None
    data["tasks"].pop(LIST_ID)
    data["lists"].pop(0)
    await entry.runtime_data.async_refresh()
    assert hass.states.get("todo.my_tasks").state == "unavailable"


async def test_diagnostics_redaction(hass, setup_integration, entry):
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["list_count"] == 1
    assert result["task_count"] == 1
    assert not any(
        value in str(result)
        for value in (
            TASK_ID,
            LIST_ID,
            "Test task",
            "fake-master",
            "account-123",
            "person@example.com",
        )
    )
