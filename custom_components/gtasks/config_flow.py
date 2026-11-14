"""OAuth flow extended with native Tasks credentials for schedule access."""

import logging
import re
import secrets
from collections.abc import Mapping
from typing import Any

import aiohttp
import probatio
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_EMAIL, CONF_TOKEN
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import (
    CONF_ACCOUNT_ID,
    CONF_DEVICE_ID,
    CONF_MASTER_TOKEN,
    DOMAIN,
    NETWORK_TIMEOUT,
    OAUTH2_SCOPES,
    TASKS_URL,
)
from .exceptions import NativeAuthError, TasksError, WrongAccount
from .native import NativeClient, obtain_master_token

_LOGGER = logging.getLogger(__name__)


async def get_oauth_info(session, access_token):
    """Check identity and public Tasks access without retaining provider errors."""
    results = []
    for url in ("https://www.googleapis.com/oauth2/v2/userinfo", TASKS_URL + "/users/@me/lists"):
        try:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=NETWORK_TIMEOUT),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise NativeAuthError("Google OAuth or Tasks API access was rejected.")
                if response.status != 200:
                    raise TasksError("Could not contact Google Tasks.")
                data = await response.json()
                if not isinstance(data, dict):
                    raise TasksError("Google returned an unexpected response.")
                results.append(data)
        except aiohttp.ClientError, TimeoutError, ValueError:
            raise TasksError("Could not contact Google Tasks.") from None
    info, lists = results
    if not isinstance(info.get("id"), str) or not info["id"]:
        raise TasksError("Google did not return an account identifier.")
    return info, [item["id"] for item in lists.get("items", [])]


class OAuth2FlowHandler(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Google authorization followed by a separate native token check."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self):
        return _LOGGER

    @property
    def extra_authorize_data(self):
        return {"scope": " ".join(OAUTH2_SCOPES), "access_type": "offline", "prompt": "consent"}

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        try:
            self._info, self._lists = await get_oauth_info(
                async_get_clientsession(self.hass), data[CONF_TOKEN][CONF_ACCESS_TOKEN]
            )
        except NativeAuthError:
            return self.async_abort(reason="access_not_configured")
        except TasksError:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(self._info["id"])
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
        else:
            self._abort_if_unique_id_configured()
        self._oauth_data = data
        return await self.async_step_native_setup()

    def _existing_entry(self):
        if self.source == SOURCE_REAUTH:
            return self._get_reauth_entry()
        if self.source == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return None

    async def async_step_native_setup(self, user_input=None):
        """Offer guided browser sign-in or an existing credential."""
        return self.async_show_menu(
            step_id="native_setup", menu_options=["browser_signin", "native"]
        )

    async def async_step_browser_signin(self, user_input=None):
        """Explain the browser step before collecting its temporary token."""
        if user_input is not None:
            return await self.async_step_browser_token()
        return self.async_show_form(
            step_id="browser_signin",
            description_placeholders={"signin_url": "https://accounts.google.com/EmbeddedSetup"},
        )

    async def async_step_browser_token(self, user_input=None):
        """Generate a device ID and exchange the temporary cookie automatically."""
        errors = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            temporary = user_input["oauth_token"].strip()
            if not temporary:
                errors["base"] = "invalid_browser_token"
            else:
                device_id = secrets.token_hex(8)
                try:
                    master = await self.hass.async_add_executor_job(
                        obtain_master_token, email, temporary, device_id
                    )
                except NativeAuthError:
                    errors["base"] = "invalid_browser_token"
                except TasksError:
                    errors["base"] = "browser_cannot_connect"
                else:
                    self._pending_native = {
                        CONF_EMAIL: email,
                        CONF_MASTER_TOKEN: master,
                        CONF_DEVICE_ID: device_id,
                    }
                    return await self.async_step_browser_verify()
        return self.async_show_form(
            step_id="browser_token",
            errors=errors,
            description_placeholders={"cookie_origin": "https://accounts.google.com"},
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_EMAIL): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.EMAIL)
                        ),
                        probatio.Required(probatio.Secret("oauth_token")): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD)
                        ),
                    }
                ),
                {CONF_EMAIL: self._info.get("email", "")},
            ),
        )

    async def async_step_browser_verify(self, user_input=None):
        """Retry verification using the exchanged master token, without replaying a cookie."""
        try:
            await self._validate_native(self._pending_native)
        except WrongAccount:
            self._pending_native = None
            result = await self.async_step_browser_token()
            result["errors"] = {"base": "wrong_account_browser"}
            return result
        except NativeAuthError:
            self._pending_native = None
            result = await self.async_step_browser_token()
            result["errors"] = {"base": "invalid_browser_token"}
            return result
        except TasksError:
            return self.async_show_form(
                step_id="browser_verify", errors={"base": "verification_unavailable"}
            )
        data, self._pending_native = self._pending_native, None
        return self._finish_native(data)

    async def _validate_native(self, data):
        native = NativeClient(
            self.hass,
            async_get_clientsession(self.hass),
            data[CONF_EMAIL],
            data[CONF_MASTER_TOKEN],
            data[CONF_DEVICE_ID],
        )
        await native.check_account(await native.read(), self._lists)

    def _finish_native(self, native_data):
        data = {**self._oauth_data, **native_data, CONF_ACCOUNT_ID: self._info["id"]}
        if existing := self._existing_entry():
            return self.async_update_reload_and_abort(existing, data=data)
        return self.async_create_entry(title=self._info.get("name") or "Google Tasks", data=data)

    async def async_step_native(self, user_input=None):
        return await self._native_form("native", user_input, self._existing_entry())

    async def _native_form(self, step_id, user_input, existing):
        errors = {}
        old = existing.data if existing else {}
        if user_input is not None:
            native_data = {
                CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                CONF_DEVICE_ID: user_input[CONF_DEVICE_ID].strip().lower(),
                CONF_MASTER_TOKEN: user_input.get(CONF_MASTER_TOKEN, "").strip()
                or old.get(CONF_MASTER_TOKEN, ""),
            }
            if not re.fullmatch(r"[0-9a-f]{16}", native_data[CONF_DEVICE_ID]):
                errors[CONF_DEVICE_ID] = "invalid_device_id"
            elif not native_data[CONF_MASTER_TOKEN]:
                errors[CONF_MASTER_TOKEN] = "invalid_auth"
            else:
                try:
                    await self._validate_native(native_data)
                except WrongAccount:
                    errors["base"] = "wrong_account"
                except NativeAuthError:
                    errors["base"] = "invalid_auth"
                except TasksError:
                    errors["base"] = "cannot_connect"
                else:
                    return self._finish_native(native_data)

        token_key = probatio.Secret(CONF_MASTER_TOKEN)
        schema = probatio.Schema(
            {
                probatio.Required(CONF_EMAIL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL)
                ),
                (
                    probatio.Optional(token_key) if existing else probatio.Required(token_key)
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                probatio.Required(CONF_DEVICE_ID): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_EMAIL: old.get(CONF_EMAIL, self._info.get("email", "")),
                    CONF_DEVICE_ID: old.get(CONF_DEVICE_ID, ""),
                },
            ),
            errors=errors,
            description_placeholders={
                "setup_url": "https://github.com/CU-Jon/HA-GTasks#native-credentials"
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    async def async_step_reconfigure(self, user_input=None):
        """Replace native credentials while keeping the linked OAuth account."""
        entry = self._get_reconfigure_entry()
        if not hasattr(self, "_info"):
            implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
                self.hass, entry
            )
            oauth = config_entry_oauth2_flow.OAuth2Session(self.hass, entry, implementation)
            await oauth.async_ensure_token_valid()
            try:
                self._info, self._lists = await get_oauth_info(
                    async_get_clientsession(self.hass), oauth.token[CONF_ACCESS_TOKEN]
                )
            except TasksError:
                return self.async_abort(reason="cannot_connect")
            if self._info["id"] != entry.data[CONF_ACCOUNT_ID]:
                return self.async_abort(reason="wrong_account")
            self._oauth_data = dict(entry.data)
        return await self.async_step_native_setup()
