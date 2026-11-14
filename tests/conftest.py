"""Synthetic accounts and resources only; all network traffic is mocked."""

from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.gtasks.const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN, TASKS_URL
from custom_components.gtasks.native import public_id

LIST_ID = public_id("test-list")
TASK_ID = public_id("test-task")
DUE = datetime(2026, 11, 15, 12, 35, tzinfo=ZoneInfo("America/New_York"))
ENTRY_DATA = {
    "auth_implementation": "test",
    "account_id": "account-123",
    "email": "person@example.com",
    "master_token": "fake-master-credential",
    "device_id": "0123456789abcdef",
    "token": {
        "access_token": "fake-access",
        "refresh_token": "fake-refresh",
        "expires_at": 9999999999,
    },
}


@pytest.fixture
def credentials(hass):
    implementation = config_entry_oauth2_flow.LocalOAuth2Implementation(
        hass, "test", "client-id", "client-secret", OAUTH2_AUTHORIZE, OAUTH2_TOKEN
    )
    config_entry_oauth2_flow.async_register_implementation(hass, "gtasks", implementation)
    return implementation


@pytest.fixture
def oauth_info(aioclient_mock):
    aioclient_mock.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        json={"id": "account-123", "email": "person@example.com", "name": "Test account"},
    )
    aioclient_mock.get(TASKS_URL + "/users/@me/lists", json={"items": [{"id": LIST_ID}]})


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Load this repository's integration."""


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain="gtasks", title="Test account", unique_id="account-123", data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def data():
    return {
        "lists": [{"id": LIST_ID, "title": "My TASKS"}],
        "tasks": {
            LIST_ID: [
                {
                    "id": TASK_ID,
                    "title": "Test task",
                    "status": "needsAction",
                    "notes": "Description",
                    "position": "0001",
                    "_native_due": DUE,
                }
            ]
        },
    }


@pytest.fixture
async def setup_integration(hass, entry, data):
    api = MagicMock()
    api.refresh = AsyncMock(side_effect=lambda: (deepcopy(data["lists"]), deepcopy(data["tasks"])))
    for method in ("insert", "patch", "delete", "move"):
        setattr(api, method, AsyncMock())
    with (
        patch(
            "custom_components.gtasks.config_entry_oauth2_flow.async_get_config_entry_implementation"
        ),
        patch("custom_components.gtasks.TasksApi", return_value=api),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield api
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
