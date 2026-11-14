"""Exercise the full HA task lifecycle with a private record hidden during completion."""

from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.gtasks.api import TasksApi
from custom_components.gtasks.native import NativeClient
from custom_components.gtasks.protocol import Snapshot, TaskRecord, decode_schedule

from .conftest import DUE, LIST_ID, TASK_ID


async def test_create_complete_read_reopen_restore_complete_delete(hass, entry, caplog):
    state = {"exists": False, "completed": False, "public_due": None, "record": None}
    native = NativeClient(
        hass, async_get_clientsession(hass), "person@example.com", "fake-master", "0123456789abcdef"
    )
    api = TasksApi(async_get_clientsession(hass), MagicMock(), native, "America/New_York")

    async def rest(method, path, *, body=None, params=None):
        if method == "GET" and path == "/users/@me/lists":
            return {"items": [{"id": LIST_ID, "title": "My TASKS"}]}
        if method == "POST":
            state["exists"] = True
            state["public_due"] = body.get("due")
            state["record"] = TaskRecord(
                "test-task",
                "test-list",
                body["title"],
                body.get("notes"),
                False,
                date(2026, 11, 15),
                1,
                None,
            )
            return {"id": TASK_ID}
        if method == "PATCH":
            assert "due" not in body
            state["completed"] = body["status"] == "completed"
            if not state["completed"]:
                assert body["completed"] is None
            return {}
        if method == "DELETE":
            state["exists"] = False
            return {}
        assert method == "GET"
        return {
            "items": [
                {
                    "id": TASK_ID,
                    "title": state["record"].title,
                    "status": "completed" if state["completed"] else "needsAction",
                    "due": state["public_due"],
                    "position": "0001",
                }
            ]
            if state["exists"]
            else []
        }

    async def sync(body, *, write=False):
        # Reopening reveals the original schedule; no new schedule write is needed.
        assert state["exists"] and not state["completed"]
        if write:
            records = body[0][1][2][0][0]
            schedule = decode_schedule(records[1][11][1])
            state["record"] = replace(
                state["record"], due=schedule.due, schedule_wire=[schedule.wire()], version=2
            )
            return None
        return Snapshot({"test-task": state["record"]}, None, frozenset({"test-list"}))

    api._request = AsyncMock(side_effect=rest)
    native._post = AsyncMock(side_effect=sync)
    with (
        patch(
            "custom_components.gtasks.config_entry_oauth2_flow.async_get_config_entry_implementation"
        ),
        patch("custom_components.gtasks.TasksApi", return_value=api),
        patch("custom_components.gtasks.native.asyncio.sleep", new_callable=AsyncMock),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        async def call(service, **data):
            return await hass.services.async_call(
                "todo",
                service,
                {"entity_id": "todo.my_tasks", **data},
                blocking=True,
                return_response=service == "get_items",
            )

        await call("add_item", item="Lifecycle test", due_datetime=DUE.isoformat())
        result = await call("get_items")
        assert result["todo.my_tasks"]["items"][0]["due"] == DUE.isoformat()
        private_calls = native._post.await_count
        await call("update_item", item=TASK_ID, status="completed")
        assert native._post.await_count == private_calls
        result = await call("get_items")
        assert result["todo.my_tasks"]["items"][0]["due"] == "2026-11-15"
        assert hass.states.get("todo.my_tasks").attributes["tasks_with_unverified_times"] == 0
        await call("update_item", item=TASK_ID, status="needs_action")
        result = await call("get_items")
        assert result["todo.my_tasks"]["items"][0]["due"] == DUE.isoformat()
        assert sum(call.kwargs.get("write", False) for call in native._post.call_args_list) == 1
        await call("update_item", item=TASK_ID, status="completed")
        await call("remove_item", item=TASK_ID)
        assert not state["exists"]
        assert "Native schedule coverage incomplete" not in caplog.text
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
