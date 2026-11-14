"""Mock HTTP boundaries, scheduling decisions, pagination and uncertain writes."""

from datetime import UTC, date
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.gtasks.api import TasksApi
from custom_components.gtasks.const import TASKS_URL
from custom_components.gtasks.exceptions import OutcomeUnknown, PartialChange, TasksError
from custom_components.gtasks.native import NativeClient, schedule_for_due
from custom_components.gtasks.protocol import Snapshot, TaskRecord

from .conftest import DUE, LIST_ID, TASK_ID


def snapshot(due=DUE, *, recurring=False):
    schedule = schedule_for_due(due, "America/New_York")
    record = TaskRecord(
        "test-task",
        "test-list",
        "Test task",
        "Description",
        False,
        schedule.due if schedule else None,
        123,
        [schedule.wire()] if schedule else None,
        recurring,
    )
    return Snapshot({record.task_id: record}, None, frozenset({"test-list"}))


@pytest.fixture
async def api(hass, aioclient_mock):
    oauth = MagicMock(token={"access_token": "fake-access"})
    oauth.async_ensure_token_valid = AsyncMock()
    native = MagicMock()
    native.read = AsyncMock(return_value=snapshot())
    native.check_account = AsyncMock()
    native.find = NativeClient.find
    native.set_schedule = AsyncMock()
    return TasksApi(async_get_clientsession(hass), oauth, native, "America/New_York")


async def test_pagination_and_overlay(api, aioclient_mock):
    aioclient_mock.get(
        TASKS_URL + "/users/@me/lists", json={"items": [{"id": LIST_ID, "title": "List"}]}
    )
    path = TASKS_URL + f"/lists/{LIST_ID}/tasks"
    aioclient_mock.get(
        path, json={"items": [{"id": TASK_ID, "title": "Test task", "due": "2026-11-15T00:00:00Z"}]}
    )
    lists, tasks = await api.refresh()
    assert tasks[LIST_ID][0]["_native_due"] == DUE
    api.native.check_account.assert_awaited_once()
    api.native.read.assert_awaited_once()
    assert len(lists) == 1


async def test_pages_follow_next_token(api):
    api._request = AsyncMock(side_effect=[{"items": [1], "nextPageToken": "next"}, {"items": [2]}])
    assert await api._pages("/test") == [1, 2]
    assert api._request.call_args.kwargs["params"]["pageToken"] == "next"


async def test_repeated_token_stops(api):
    api._request = AsyncMock(return_value={"items": [], "nextPageToken": "repeat"})
    with pytest.raises(TasksError, match="pagination"):
        await api._pages("/test")
    assert api._request.await_count == 2


@pytest.mark.parametrize("due", [DUE, DUE.astimezone(UTC)])
async def test_complete_does_not_touch_schedule(api, due):
    api._request = AsyncMock(return_value={})
    await api.patch(LIST_ID, TASK_ID, {"status": "completed"}, due)
    api.native.set_schedule.assert_not_awaited()
    assert api._request.call_args.kwargs["body"] == {"status": "completed"}


async def test_date_only_does_not_rewrite_timezone(api):
    api.native.read.return_value = snapshot(date(2026, 11, 15))
    api.timezone = "Europe/London"
    api._request = AsyncMock(return_value={})
    await api.patch(LIST_ID, TASK_ID, {"title": "Renamed"}, date(2026, 11, 15))
    api.native.set_schedule.assert_not_awaited()


@pytest.mark.parametrize(
    "due", [None, date(2026, 11, 16), DUE.replace(minute=59), DUE.replace(hour=0)]
)
async def test_schedule_changes(api, due):
    api._request = AsyncMock(return_value={})
    await api.patch(LIST_ID, TASK_ID, {"title": "Renamed", "due": "discard"}, due)
    api.native.set_schedule.assert_awaited_once_with(
        LIST_ID, TASK_ID, schedule_for_due(due, api.timezone)
    )
    assert "due" not in api._request.call_args.kwargs["body"]


async def test_recurring_refused_before_content_write(api):
    api.native.read.return_value = snapshot(recurring=True)
    api._request = AsyncMock()
    with pytest.raises(TasksError, match="recurring"):
        await api.patch(LIST_ID, TASK_ID, {"title": "Do not change"}, None)
    api._request.assert_not_awaited()


async def test_recurring_completion_supported(api):
    api.native.read.return_value = snapshot(recurring=True)
    api._request = AsyncMock(return_value={})
    await api.patch(LIST_ID, TASK_ID, {"status": "completed"}, DUE)
    api.native.set_schedule.assert_not_awaited()


async def test_insert_partial_failure_no_retry(api):
    api._request = AsyncMock(return_value={"id": TASK_ID})
    api.native.set_schedule.side_effect = OutcomeUnknown()
    with pytest.raises(PartialChange, match="created"):
        await api.insert(LIST_ID, {"title": "Test"}, DUE)
    api._request.assert_awaited_once()
    api.native.set_schedule.assert_awaited_once()


async def test_patch_partial_failure(api):
    api._request = AsyncMock(return_value={})
    api.native.set_schedule.side_effect = OutcomeUnknown()
    with pytest.raises(PartialChange, match="details were updated"):
        await api.patch(LIST_ID, TASK_ID, {"title": "New"}, None)
    api._request.assert_awaited_once()


@pytest.mark.parametrize("status", [400, 403, 404, 429, 503])
async def test_public_errors_do_not_expose_provider_body(api, aioclient_mock, status):
    aioclient_mock.post(TASKS_URL + "/test", status=status, text="sensitive-provider-response")
    with pytest.raises(TasksError) as error:
        await api._request("POST", "/test", body={})
    assert "sensitive" not in str(error.value)
    assert aioclient_mock.call_count == 1


async def test_timeout_is_unknown_and_not_retried(api, aioclient_mock):
    aioclient_mock.post(TASKS_URL + "/test", exc=aiohttp.ServerDisconnectedError())
    with pytest.raises(OutcomeUnknown):
        await api._request("POST", "/test", body={})
    assert aioclient_mock.call_count == 1


async def test_missing_private_record_fails_refresh(api, aioclient_mock):
    api.native.read.return_value = Snapshot({}, None)
    aioclient_mock.get(TASKS_URL + "/users/@me/lists", json={"items": [{"id": LIST_ID}]})
    aioclient_mock.get(TASKS_URL + f"/lists/{LIST_ID}/tasks", json={"items": [{"id": TASK_ID}]})
    with pytest.raises(TasksError, match="not yet visible"):
        await api.refresh()
