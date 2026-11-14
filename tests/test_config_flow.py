"""Config-flow manager tests for OAuth, native validation and credential changes."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.gtasks.const import TASKS_URL
from custom_components.gtasks.exceptions import NativeAuthError, TasksError, WrongAccount

from .conftest import ENTRY_DATA

pytestmark = pytest.mark.usefixtures("current_request_with_host")

NATIVE_INPUT = {key: ENTRY_DATA[key] for key in ("email", "master_token", "device_id")}


async def authorize(hass, credentials, flow_id=None, *, guided=False):
    if flow_id is None:
        result = await hass.config_entries.flow.async_init(
            "gtasks", context={"source": config_entries.SOURCE_USER}
        )
    else:
        result = await hass.config_entries.flow.async_configure(flow_id, {})
    # No browser request context: explicitly choose the OAuth implementation.
    if result.get("step_id") == "pick_implementation":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"implementation": "test"}
        )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert "userinfo.email" in result["url"]
    with patch.object(
        credentials,
        "async_resolve_external_data",
        return_value={**ENTRY_DATA["token"], "expires_in": 3600},
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "mock-code"}
        )
        assert result["type"] is FlowResultType.EXTERNAL_STEP_DONE
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    if result.get("step_id") == "native_setup" and not guided:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "native"}
        )
    return result


async def test_full_flow(hass, credentials, oauth_info):
    result = await authorize(hass, credentials)
    assert result["step_id"] == "native"
    assert "fake-master" not in str(result)
    with (
        patch("custom_components.gtasks.config_flow.NativeClient") as native,
        patch("custom_components.gtasks.async_setup_entry", new_callable=AsyncMock),
    ):
        native.return_value.read = AsyncMock()
        result = await hass.config_entries.flow.async_configure(result["flow_id"], NATIVE_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "account-123"
    assert result["data"]["master_token"] == ENTRY_DATA["master_token"]
    native.return_value.validate_account.assert_called_once()


@pytest.mark.parametrize(
    "error,code",
    [
        (NativeAuthError("safe"), "invalid_auth"),
        (WrongAccount("safe"), "wrong_account"),
        (TasksError("safe"), "cannot_connect"),
    ],
)
async def test_native_errors_keep_form(hass, credentials, oauth_info, error, code):
    result = await authorize(hass, credentials)
    with patch("custom_components.gtasks.config_flow.NativeClient") as native:
        native.return_value.read = AsyncMock(side_effect=error)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], NATIVE_INPUT)
    assert result["step_id"] == "native"
    assert result["errors"] == {"base": code}
    assert "fake-master" not in str(result)


async def test_invalid_device_id(hass, credentials, oauth_info):
    result = await authorize(hass, credentials)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**NATIVE_INPUT, "device_id": "invalid"}
    )
    assert result["errors"] == {"device_id": "invalid_device_id"}


async def test_duplicate(hass, entry, credentials, oauth_info):
    result = await authorize(hass, credentials)
    assert result["reason"] == "already_configured"


async def test_reauth_same_account(hass, entry, credentials, oauth_info):
    result = await hass.config_entries.flow.async_init(
        "gtasks",
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    result = await authorize(hass, credentials, result["flow_id"])
    assert result["step_id"] == "native"
    with (
        patch("custom_components.gtasks.config_flow.NativeClient") as native,
        patch("homeassistant.config_entries.ConfigEntries.async_reload", return_value=True),
    ):
        native.return_value.read = AsyncMock()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": ENTRY_DATA["email"], "device_id": ENTRY_DATA["device_id"]}
        )
    assert result["reason"] == "reauth_successful"
    assert entry.data["master_token"] == ENTRY_DATA["master_token"]


async def test_reauth_wrong_oauth_account(hass, entry, credentials, aioclient_mock):
    aioclient_mock.get(
        "https://www.googleapis.com/oauth2/v2/userinfo", json={"id": "different-account"}
    )
    aioclient_mock.get(TASKS_URL + "/users/@me/lists", json={})
    result = await hass.config_entries.flow.async_init(
        "gtasks",
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await authorize(hass, credentials, result["flow_id"])
    assert result["reason"] == "wrong_account"
    assert entry.data == ENTRY_DATA


async def test_reconfigure_keeps_secret(hass, entry, credentials, oauth_info):
    result = await hass.config_entries.flow.async_init(
        "gtasks", context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["step_id"] == "native_setup"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "native"}
    )
    assert "fake-master" not in str(result)
    with (
        patch("custom_components.gtasks.config_flow.NativeClient") as native,
        patch("homeassistant.config_entries.ConfigEntries.async_reload", return_value=True),
    ):
        native.return_value.read = AsyncMock()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": ENTRY_DATA["email"], "device_id": ENTRY_DATA["device_id"]}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["master_token"] == ENTRY_DATA["master_token"]


async def test_public_api_disabled(hass, credentials, aioclient_mock):
    aioclient_mock.get(
        "https://www.googleapis.com/oauth2/v2/userinfo", status=403, text="sensitive-provider-error"
    )
    result = await authorize(hass, credentials)
    assert result["reason"] == "access_not_configured"
    assert "sensitive" not in str(result)
