# Google Tasks with Times

Use Google Tasks in Home Assistant's **To-do lists**, with support for due dates and times.

- One to-do entity per task list, with new lists discovered automatically.
- Create, rename, describe, complete, reopen, delete and reorder tasks.
- Set due times or switch a timed task back to a date-only task.
- Keep the original schedule when editing task details or completing a task.
- Connect multiple Google accounts.
- Show all active tasks and a configurable amount of completed history.

Requires **Home Assistant 2026.11.0 or newer** and a Google account. This is a custom integration and is not affiliated with Google or Home Assistant.

## Installation

<!-- ha-marketplace:exclude-start -->

### Marketplace / HACS

[![Open Google Tasks with Times in your Home Assistant Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=CU-Jon&repository=HA-GTasks&category=integration)

Open the link, add the custom repository if prompted, and install **Google Tasks with Times**. Follow any restart prompt, then continue with [Setup](#setup).

In HACS, you can also add `https://github.com/CU-Jon/HA-GTasks` under **Custom repositories** with the category **Integration**.

### Manual installation

Download `gtasks.zip` from the [latest stable release](https://github.com/CU-Jon/HA-GTasks/releases/latest) and extract it into your Home Assistant configuration directory. The resulting path should be `custom_components/gtasks/manifest.json`. Restart Home Assistant.

Alternatively, copy the repository's `custom_components/gtasks` directory into your configuration's `custom_components` directory.

<!-- ha-marketplace:exclude-end -->

## Setup

### Google OAuth

1. Follow the [Google Tasks prerequisites](https://www.home-assistant.io/integrations/google_tasks/#prerequisites) to create a Google Cloud project, enable **Google Tasks API**, configure the consent screen, and create an OAuth client of type **Web application**.
2. Add `https://my.home-assistant.io/redirect/oauth` as an authorized redirect URI. Make sure [My Home Assistant](https://my.home-assistant.io/) points to your Home Assistant instance.
3. Open **Settings → Devices & services → Add integration** and select **Google Tasks with Times**.
4. Enter your OAuth client ID and secret when prompted, then authorize your Google account.
5. Continue with the task-time authentication below, using the same Google account.

If your consent screen is in **Testing** mode, add your Google account as a test user. Testing refresh tokens may expire; follow the linked Google Tasks instructions for configuring the consent screen.

This integration can coexist with the built-in Google Tasks integration. It creates separate entities and requires its own setup; existing entities and automations are not migrated automatically.

### Native credentials

Choose **Obtain a token with guided Google sign-in** during setup. Use Chrome or Edge on a computer and follow the wizard to:

1. Sign in to Google in a separate tab, including any two-step verification.
2. Copy the temporary **`oauth_token` cookie value** from the browser's Developer Tools, as shown in the wizard.
3. Submit that value and your Google email to Home Assistant.

Home Assistant obtains an Android master token, generates a matching device ID, and verifies access to your task lists. Your Google password stays on Google's website. The temporary cookie is not stored after setup.

If verification temporarily fails after the token exchange, select **Submit** to retry with the token already obtained. If no shared task is available for verification, create an ordinary incomplete task in Google Tasks and retry.

You can instead choose **Use an existing master token** and supply its email, master token and matching 16-character hexadecimal device ID.

To replace these credentials later, use the integration's **Reconfigure** menu. Reauthentication must use the same Google account.

## Using task times

Open Home Assistant's **To-do lists**, select a list, and use the task editor to set a due date and time. Automations can use the standard `todo.add_item` and `todo.update_item` actions.

Create a timed task:

```yaml
action: todo.add_item
target:
  entity_id: todo.my_tasks
data:
  item: Pick up groceries
  due_datetime: "2026-11-16T12:35:00-05:00"
```

Remove its time while keeping the date:

```yaml
action: todo.update_item
target:
  entity_id: todo.my_tasks
data:
  item: Pick up groceries
  due_date: "2026-11-16"
```

Replace `todo.my_tasks` with your entity ID. Supply either `due_date` or `due_datetime` in a call. Times are converted to Home Assistant's configured timezone; include a UTC offset to identify the intended instant during daylight-saving transitions. Times have minute precision, so seconds must be zero. Midnight is supported.

### Completed task history

Each list displays all active top-level tasks and, by default, its **100 most recently completed tasks**, with no age cutoff.

Open **Settings → Devices & services → Google Tasks with Times → Configure** (Options) to change **Maximum completed tasks per list**. Choose **0–500**; **0** hides completed tasks. Saving reloads the integration.

The limit applies to both the to-do page and to-do actions. It does not delete anything from Google Tasks. Older completed tasks remain available in Google Tasks; reopening one there makes it active and visible in Home Assistant again. The entity's `completed_tasks_hidden` attribute reports the number omitted.

Completed tasks display their due date without a time. Completing a task preserves its schedule; reopening it lets its time appear again after refresh. Reopen a task before changing its schedule.

## Updates and limitations

- Lists refresh every **30 minutes** and after changes made through this integration. Use `homeassistant.update_entity` to request a refresh sooner.
- Subtasks are not displayed.
- Recurring tasks can be displayed and completed, but their schedules cannot be changed through this integration.
- Time support uses an undocumented Google API, which may change or become unavailable. An active task whose time cannot be verified remains visible with its due date. Its schedule cannot be edited until verification succeeds, but ordinary content and completion edits remain available. The `tasks_with_unverified_times` attribute reports affected active tasks.
- An error or timeout can occur after part of a change has reached Google. Refresh and check the task before repeating the action, especially when creating a task.

## Credentials and privacy

Task-time access requires an **Android master token**, a powerful Google account credential. Treat it as a password. Submit credentials only through your trusted HTTPS Home Assistant address, and protect your Home Assistant storage and backups. Credentials are stored locally with the integration configuration and are not separately encrypted by this integration.

Never share master tokens, OAuth secrets, cookies or raw browser network captures in issue reports. Integration diagnostics contain counts and status information without task titles, account identifiers or credentials.

Removing this integration does not delete Google tasks or revoke its Google account credentials. Manage account access separately in your Google account settings.

## Help

For setup or task-time problems, check Home Assistant's logs and [open an issue](https://github.com/CU-Jon/HA-GTasks/issues). Include your Home Assistant version, integration version, the error message and steps to reproduce. Review any attached logs or screenshots for personal information before sharing them.

## License

[Apache 2.0](LICENSE). See [NOTICE](NOTICE) for third-party attribution.
