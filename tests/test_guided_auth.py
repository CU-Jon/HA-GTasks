"""Guided token acquisition through the real flow manager with synthetic credentials."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.gtasks.exceptions import NativeAuthError, TasksError, WrongAccount

from .conftest import ENTRY_DATA
from .test_config_flow import authorize

pytestmark = pytest.mark.usefixtures("current_request_with_host")
COOKIE = {"email": "person@example.com", "oauth_token": "temporary-test-cookie"}


async def start_guided(hass, credentials, *, entry=None):
    if entry:
        result = await hass.config_entries.flow.async_init(
            "gtasks",
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
    else:
        result = await authorize(hass, credentials, guided=True)
    assert result["step_id"] == "native_setup"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "browser_signin"}
    )
    assert result["step_id"] == "browser_signin"
    assert (
        result["description_placeholders"]["signin_url"]
        == "https://accounts.google.com/EmbeddedSetup"
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "browser_token"
    return result


async def test_guided_success(hass, credentials, oauth_info):
    result = await start_guided(hass, credentials)
    with (
        patch(
            "custom_components.gtasks.config_flow.obtain_master_token",
            return_value="new-test-master",
        ) as exchange,
        patch("custom_components.gtasks.config_flow.NativeClient") as native,
        patch("custom_components.gtasks.async_setup_entry", new_callable=AsyncMock),
    ):
        native.return_value.read = AsyncMock()
        native.return_value.check_account = AsyncMock()
        result = await hass.config_entries.flow.async_configure(result["flow_id"], COOKIE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["master_token"] == "new-test-master"
    assert len(bytes.fromhex(data["device_id"])) == 8
    exchange.assert_called_once_with(COOKIE["email"], COOKIE["oauth_token"], data["device_id"])
    assert "oauth_token" not in data
    assert "temporary-test-cookie" not in str(data)
    native.return_value.check_account.assert_awaited_once()


@pytest.mark.parametrize(
    "error,expected",
    [
        (NativeAuthError("safe"), "invalid_browser_token"),
        (TasksError("safe"), "verification_unavailable"),
        (WrongAccount("safe"), "wrong_account_browser"),
    ],
)
async def test_guided_validation_error_discards_credentials(
    hass, credentials, oauth_info, error, expected
):
    result = await start_guided(hass, credentials)
    with (
        patch(
            "custom_components.gtasks.config_flow.obtain_master_token",
            return_value="new-test-master",
        ),
        patch("custom_components.gtasks.config_flow.NativeClient") as native,
    ):
        native.return_value.read = AsyncMock(side_effect=error)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], COOKIE)
    assert result["errors"] == {"base": expected}
    assert "temporary-test-cookie" not in str(result)
    assert "new-test-master" not in str(result)
    assert not hass.config_entries.async_entries("gtasks")


@pytest.mark.parametrize(
    "error,expected",
    [
        (NativeAuthError("safe"), "invalid_browser_token"),
        (TasksError("safe"), "browser_cannot_connect"),
    ],
)
async def test_exchange_failure_not_retried(hass, credentials, oauth_info, error, expected):
    result = await start_guided(hass, credentials)
    with patch(
        "custom_components.gtasks.config_flow.obtain_master_token", side_effect=error
    ) as exchange:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], COOKIE)
    exchange.assert_called_once()
    assert result["errors"] == {"base": expected}
    assert "temporary-test-cookie" not in str(result)


async def test_reconfigure_guided_replaces_credentials(hass, entry, credentials, oauth_info):
    result = await start_guided(hass, credentials, entry=entry)
    with (
        patch(
            "custom_components.gtasks.config_flow.obtain_master_token",
            return_value="replacement-master",
        ),
        patch("custom_components.gtasks.config_flow.NativeClient") as native,
        patch("homeassistant.config_entries.ConfigEntries.async_reload", return_value=True),
    ):
        native.return_value.read = AsyncMock()
        native.return_value.check_account = AsyncMock()
        result = await hass.config_entries.flow.async_configure(result["flow_id"], COOKIE)
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["master_token"] == "replacement-master"
    assert entry.data["device_id"] != ENTRY_DATA["device_id"]
    assert "oauth_token" not in entry.data


async def test_verification_retry_does_not_reexchange_cookie(hass, credentials, oauth_info):
    result = await start_guided(hass, credentials)
    with (
        patch(
            "custom_components.gtasks.config_flow.obtain_master_token",
            return_value="pending-test-master",
        ) as exchange,
        patch("custom_components.gtasks.config_flow.NativeClient") as native,
        patch("custom_components.gtasks.async_setup_entry", new_callable=AsyncMock),
    ):
        native.return_value.read = AsyncMock(
            side_effect=[TasksError("network unavailable"), object()]
        )
        native.return_value.check_account = AsyncMock()
        result = await hass.config_entries.flow.async_configure(result["flow_id"], COOKIE)
        assert result["step_id"] == "browser_verify"
        assert "pending-test-master" not in str(result)
        assert "temporary-test-cookie" not in str(result)
        assert not hass.config_entries.async_entries("gtasks")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["master_token"] == "pending-test-master"
    exchange.assert_called_once()


async def test_default_list_verification_uses_oauth_only_for_public_api(
    hass, credentials, oauth_info, aioclient_mock
):
    from custom_components.gtasks.const import SYNC_URL, TASKS_URL
    from custom_components.gtasks.native import public_id

    from .conftest import LIST_ID
    from .test_protocol import CAPTURE

    result = await start_guided(hass, credentials)
    aioclient_mock.post(SYNC_URL, json=CAPTURE["sources"]["tasks"]["noon"]["response"])
    aioclient_mock.get(
        TASKS_URL + f"/lists/{LIST_ID}/tasks",
        json={"items": [{"id": public_id("private-test-task")}]},
    )
    with (
        patch(
            "custom_components.gtasks.config_flow.obtain_master_token", return_value="test-master"
        ),
        patch(
            "custom_components.gtasks.native.exchange_token",
            return_value=("native-scoped-token", 600),
        ),
        patch("custom_components.gtasks.async_setup_entry", new_callable=AsyncMock),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], COOKIE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    for _method, url, _body, headers in aioclient_mock.mock_calls:
        if str(url).startswith(TASKS_URL):
            assert headers["Authorization"] == "Bearer fake-access"
        elif str(url) == SYNC_URL:
            assert headers["Authorization"] == "Bearer native-scoped-token"
