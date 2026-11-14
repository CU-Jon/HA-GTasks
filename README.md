# Google Tasks with Times

A Home Assistant custom integration based on the built-in Google Tasks integration, with support for **due dates and due times** in Home Assistant's standard To-do lists.

Requires **Home Assistant 2026.11.0 or newer**. The integration domain is `gtasks`, so it can coexist with the built-in `google_tasks` integration. It creates separate entities; existing entity IDs and automations are not migrated automatically.

## Features

- One To-do entity per Google task list, with additional lists discovered on refresh.
- Create, rename, describe, complete, delete and reorder tasks.
- Read and write local due times, including midnight and nonzero minutes.
- Change a timed task back to a date-only task.
- Preserve a task's time when completing it or editing its description.
- Support multiple Google accounts, with matching public and native credentials checked during setup.

Google's public Tasks API discards times. This integration uses normal Google OAuth for task contents and an Android master token to request a separate Tasks-scoped token for the private scheduling API. The private API is undocumented and may change without notice. This project is not affiliated with Google or Home Assistant.

## Installation

<!-- ha-marketplace:exclude-start -->

### Marketplace / HACS

[![Open Google Tasks with Times in your Home Assistant Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=CU-Jon&repository=HA-GTasks&category=integration)

Select the button to open this repository in your Home Assistant Marketplace. If prompted, approve adding it as a custom repository. Install **Google Tasks with Times**, follow any restart prompt, then continue with [Setup](#setup).

For HACS, you can also add `https://github.com/CU-Jon/HA-GTasks` under **Custom repositories**, choose **Integration**, and download **Google Tasks with Times**.

### Manual installation

Copy `custom_components/gtasks` into the `custom_components` directory of your Home Assistant configuration and restart Home Assistant. Alternatively, extract the `gtasks.zip` produced by `python scripts/package.py` into your configuration directory; it contains `custom_components/gtasks/`.

<!-- ha-marketplace:exclude-end -->

## Setup

### Google OAuth

1. Follow the official [Google Tasks OAuth setup instructions](https://www.home-assistant.io/integrations/google_tasks/#prerequisites): create a Google Cloud project, enable **Google Tasks API**, configure its consent screen, and create an OAuth client of type **Web application**.
2. Add `https://my.home-assistant.io/redirect/oauth` as an authorized redirect URI. Your [My Home Assistant](https://my.home-assistant.io/) instance URL must point to the installation you are configuring.
3. In **Settings → Devices & services → Add integration**, select **Google Tasks with Times**. Enter the OAuth client ID and secret when prompted, then authorize your Google account.
4. Choose **Obtain a token with guided Google sign-in**. The next screens explain how to sign in to Google and copy its temporary sign-in cookie. Home Assistant generates the device ID, obtains the master token, and verifies the account automatically after you submit the cookie. You can also choose **Use an existing master token**.

If using an external consent screen in Testing mode, add your account as a test user. Google may expire testing refresh tokens; see the upstream instructions for moving the consent screen to production for personal use. Application credentials belong to this custom integration; an existing built-in Google Tasks entry is not imported.

### Native credentials

The recommended setup wizard needs Chrome or Edge on a computer. It walks you through Google's sign-in (including two-step verification), then shows where to find the temporary `oauth_token` cookie in Developer Tools. Paste only that cookie's value into the protected field in Home Assistant. Use your trusted HTTPS Home Assistant address when submitting credentials. Home Assistant automatically exchanges it for an **Android master token** and generates the matching **16-character hexadecimal device ID**. The temporary cookie is not stored in the configuration entry or shown again after submission.

Google sign-in and copying the cookie remain manual: the Home Assistant page cannot read cookies from Google's website. The exchange and account checks happen inside Home Assistant, without a terminal, separate script, or Google password being supplied to this integration. If verification is temporarily unavailable after a successful exchange, select **Submit** to retry with the master token already obtained. It stays in memory until setup succeeds or is cancelled; you do not need to copy another cookie. A rejected token or confirmed different account requires a fresh sign-in cookie.

Account verification compares task-list identifiers, not their names. Renaming the default list does not affect identity. If the private API identifiers cannot establish a match, the integration checks the native token's lists through Google's public Tasks API. Empty responses or denied verification requests are reported as unavailable verification, not as proof of a different account.

You may reuse an existing Android master token and its matching device ID that you control. The integration exchanges it for a Tasks-scoped access token using the Google Tasks Android app identity. It does not access Keep or need a running phone, ADB, a HAR capture, or browser cookies after setup.

As an alternative to the wizard, you can obtain a token with the optional local helper. Use Python 3.14 or newer in a private terminal:

```console
python -m venv .venv
```

Activate that environment (`.venv\Scripts\activate` on Windows, or `source .venv/bin/activate` on Linux/macOS), then run:

```console
python -m pip install gpsoauth==3.0.0
python tools/get_master_token.py --output .local/google-tasks-credentials.json
```

The helper explains how to obtain Google's temporary `oauth_token` cookie in your own browser. It accepts that cookie through hidden terminal input and writes the resulting master token to the specified local file. Open that file privately and enter its three values into Home Assistant. No credentials are printed. Delete the local copy after setup if you do not need it for recovery. The helper refuses to overwrite an existing file.

**Treat the master token as a password.** It is a powerful Google account credential. Home Assistant stores it with the configuration entry in `.storage`, alongside the OAuth credentials. It is not encrypted separately by this integration; protect your Home Assistant storage and backups. The integration's diagnostics include counts only, and never export credentials, account identifiers, titles or notes. Do not attach credential files, HARs or debug HTTP captures to public issues.

To replace native credentials later, use the integration's **Reconfigure** menu. Both guided sign-in and existing-token entry are available there and during reauthentication. Leaving the master-token field blank when updating existing credentials retains the stored value. Reauthentication requires the same Google account.

## Using task times

Use the standard Home Assistant To-do editor or `todo.add_item` / `todo.update_item` actions. For example:

```yaml
action: todo.add_item
target:
  entity_id: todo.my_tasks
data:
  item: Pick up groceries
  due_datetime: "2026-11-16T12:35:00-05:00"
```

To remove the time while keeping the day:

```yaml
action: todo.update_item
target:
  entity_id: todo.my_tasks
data:
  item: Pick up groceries
  due_date: "2026-11-16"
```

Use either `due_date` or `due_datetime` in a call. Date-times are converted to Home Assistant's configured timezone for writing. Explicit UTC offsets identify the intended instant at daylight-saving transitions. Google Tasks supports minute precision; seconds must be zero. Midnight is a timed task, distinct from a task with only a date.

## Behavior and limitations

- Refreshes every 30 minutes and after mutations. Use `homeassistant.update_entity` to request a refresh. One private account snapshot is shared by all lists.
- Schedule writes use a freshly read task version and are checked by an independent read. Writes are never replayed automatically. A timeout can mean the change happened; refresh before retrying.
- Creating a timed task and updating task contents plus its schedule require separate Google requests. An error may report that the content change succeeded while the schedule remains unconfirmed.
- Recurring tasks can be read and completed. Changing their schedule is refused to avoid modifying recurrence rules or a whole series accidentally.
- Subtasks are hidden, following the upstream integration. Shared/assigned tasks unavailable to the private endpoint cause a refresh error rather than silently losing their time.
- Timed reads/writes, date changes, midnight and clearing just the time were previously verified against Google on disposable tasks. This Home Assistant adapter has automated tests with mocked Google responses. Installation and end-to-end behavior in a live Home Assistant instance still need validation. Clearing the entire due date uses the same field-mask protocol but has not yet been verified against Google.
- If Google changes the private protocol, entities become unavailable instead of falling back to date-only writes. Removing this integration does not delete Google tasks or revoke Google account credentials; manage grants in your Google account separately.

## Development

Use Linux and Python **3.14.2+** (the Home Assistant runtime requirement):

```console
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
pytest
python scripts/package.py
```

The test harness is the official `pytest-homeassistant` matching Home Assistant 2026.11.0. Tests use synthetic accounts and sanitized protocol fixtures with external network access disabled. CI also runs Home Assistant's integration validator and the repository compatibility validator. Do not commit raw HAR files, APKs, credentials or generated archives; these are ignored by Git.

The neutral icon source is `assets/icon.svg`. Run `python scripts/render_brand.py` in the test environment to regenerate the local 256px and 512px brand images.

## Credits and license

Derived from [Home Assistant Core's Google Tasks integration](https://github.com/home-assistant/core/tree/2026.11.0/homeassistant/components/google_tasks), licensed under Apache 2.0. The public transport is adapted to Home Assistant's asynchronous HTTP session. See [NOTICE](NOTICE) and [LICENSE](LICENSE).

Private API research started from [magic-apps' Google Tasks notes](https://github.com/alxhslm/magic-apps/blob/main/docs/magical/google-tasks.md) and was verified using independently captured Calendar and Tasks requests. No raw captures or account data are distributed here.
