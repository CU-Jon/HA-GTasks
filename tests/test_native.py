"""Native account binding, auth cache, readback and no write replay."""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.gtasks.const import SYNC_URL
from custom_components.gtasks.exceptions import (
    AccountVerificationError,
    NativeAuthError,
    OutcomeUnknown,
    TasksError,
    WrongAccount,
)
from custom_components.gtasks.native import NativeClient, schedule_for_due
from custom_components.gtasks.protocol import Snapshot

from .conftest import DUE, ENTRY_DATA, LIST_ID, TASK_ID
from .test_api import snapshot


@pytest.fixture
async def native(hass, aioclient_mock):
    return NativeClient(
        hass,
        async_get_clientsession(hass),
        ENTRY_DATA["email"],
        ENTRY_DATA["master_token"],
        ENTRY_DATA["device_id"],
    )


def test_wrong_account(native):
    with pytest.raises(AccountVerificationError):
        native.validate_account(Snapshot({}, None, frozenset({"someone-else"})), [LIST_ID])
    with pytest.raises(AccountVerificationError):
        native.validate_account(Snapshot({}, None), [LIST_ID])
    native.validate_account(Snapshot({}, None, list_ids=frozenset({"test-list"})), [LIST_ID])
    native.validate_account(snapshot(), [LIST_ID])


async def test_renamed_default_list_verified_with_public_ids(native, aioclient_mock):
    from custom_components.gtasks.const import TASKS_URL

    native._access_token = AsyncMock(return_value="fake-token")
    aioclient_mock.get(
        TASKS_URL + "/users/@me/lists",
        json={"items": [{"id": LIST_ID, "title": "Renamed default list"}]},
    )
    await native.check_account(
        Snapshot({}, None, frozenset({"different-private-representation"})), [LIST_ID]
    )
    assert aioclient_mock.call_count == 1


async def test_public_account_actually_mismatched(native, aioclient_mock):
    from custom_components.gtasks.const import TASKS_URL

    native._access_token = AsyncMock(return_value="fake-token")
    aioclient_mock.get(
        TASKS_URL + "/users/@me/lists", json={"items": [{"id": "other-account-list"}]}
    )
    with pytest.raises(WrongAccount):
        await native.check_account(Snapshot({}, None), [LIST_ID])


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_unavailable_verification_is_not_wrong_account(
    native, aioclient_mock, status, caplog
):
    from custom_components.gtasks.const import TASKS_URL

    native._access_token = AsyncMock(return_value="fake-token")
    aioclient_mock.get(
        TASKS_URL + "/users/@me/lists", status=status, text="sensitive-provider-body"
    )
    with pytest.raises(AccountVerificationError):
        await native.check_account(Snapshot({}, None), [LIST_ID])
    assert "sensitive-provider-body" not in caplog.text
    assert "fake-token" not in caplog.text


async def test_private_id_match_needs_no_extra_request(native, aioclient_mock):
    await native.check_account(snapshot(), [LIST_ID])
    assert aioclient_mock.call_count == 0


async def test_empty_public_response_cannot_prove_wrong_account(native, aioclient_mock):
    from custom_components.gtasks.const import TASKS_URL

    native._access_token = AsyncMock(return_value="fake-token")
    aioclient_mock.get(TASKS_URL + "/users/@me/lists", json={})
    with pytest.raises(AccountVerificationError):
        await native.check_account(Snapshot({}, None), [LIST_ID])


async def test_verification_checks_later_pages(native, aioclient_mock):
    from custom_components.gtasks.const import TASKS_URL

    native._access_token = AsyncMock(return_value="fake-token")
    aioclient_mock.get(
        TASKS_URL + "/users/@me/lists",
        params={"maxResults": 100, "pageToken": "next"},
        json={"items": [{"id": LIST_ID}]},
    )
    aioclient_mock.get(
        TASKS_URL + "/users/@me/lists",
        params={"maxResults": 100},
        json={"items": [{"id": "another-list"}], "nextPageToken": "next"},
    )
    await native.check_account(Snapshot({}, None), [LIST_ID])
    assert aioclient_mock.call_count == 2


def test_direct_public_identifiers(native):
    native.validate_account(Snapshot({}, None, frozenset({LIST_ID})), [LIST_ID])
    record = snapshot().tasks["test-task"]
    direct = Snapshot({TASK_ID: replace(record, task_id=TASK_ID, list_id=LIST_ID)}, None)
    assert native.find(direct, TASK_ID, LIST_ID).task_id == TASK_ID


async def test_token_cache_and_refresh(native):
    with patch(
        "custom_components.gtasks.native.exchange_token", return_value=("scoped-token", 600)
    ) as exchange:
        assert await native._access_token() == "scoped-token"
        assert await native._access_token() == "scoped-token"
        assert exchange.call_count == 1
        native._expires = 0
        await native._access_token()
        assert exchange.call_count == 2


async def test_verified_write_ignores_ack_shape(native):
    before = snapshot()
    desired = schedule_for_due(DUE.replace(minute=45), "America/New_York")
    after = Snapshot(
        {"test-task": replace(before.tasks["test-task"], schedule_wire=[desired.wire()])}, None
    )
    native.read = AsyncMock(side_effect=[before, after])
    native._post = AsyncMock(return_value=None)
    with patch("custom_components.gtasks.native.asyncio.sleep", new_callable=AsyncMock):
        await native.set_schedule(LIST_ID, TASK_ID, desired)
    native._post.assert_awaited_once()
    assert native._post.call_args.kwargs["write"] is True


async def test_ignored_write_is_unknown(native):
    native.read = AsyncMock(return_value=snapshot())
    native._post = AsyncMock()
    with (
        patch("custom_components.gtasks.native.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(OutcomeUnknown),
    ):
        await native.set_schedule(LIST_ID, TASK_ID, None)
    native._post.assert_awaited_once()


async def test_content_change_detected(native):
    before = snapshot()
    record = before.tasks["test-task"]
    after = Snapshot({"test-task": replace(record, title="Unexpected content")}, None)
    native.read = AsyncMock(side_effect=[before, after])
    native._post = AsyncMock()
    with (
        patch("custom_components.gtasks.native.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(OutcomeUnknown),
    ):
        await native.set_schedule(LIST_ID, TASK_ID, record.schedule)


async def test_native_401_invalidates_token(native, aioclient_mock):
    native._token, native._expires = "cached-token", float("inf")
    aioclient_mock.post(SYNC_URL, status=401, text="sensitive")
    with pytest.raises(NativeAuthError):
        await native.read()
    assert native._token is None


async def test_rate_limit_has_cooldown(native, aioclient_mock):
    native._access_token = AsyncMock(return_value="fake")
    aioclient_mock.post(SYNC_URL, status=429, headers={"Retry-After": "120"})
    for _ in range(2):
        with pytest.raises(TasksError, match="rate limited"):
            await native.read()
    assert aioclient_mock.call_count == 1


async def test_post_500_write_is_unknown(native, aioclient_mock):
    native._access_token = AsyncMock(return_value="fake")
    aioclient_mock.post(SYNC_URL, status=500, text="sensitive")
    with pytest.raises(OutcomeUnknown):
        await native._post([], write=True)
    assert aioclient_mock.call_count == 1
