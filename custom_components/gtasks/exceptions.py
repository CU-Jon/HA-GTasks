"""Provider failures with messages that contain no provider response or credentials."""

from homeassistant.exceptions import HomeAssistantError


class TasksError(HomeAssistantError):
    """A safe API or schema failure."""


class NativeAuthError(TasksError):
    """Native credentials were rejected."""


class WrongAccount(TasksError):
    """OAuth and native credentials belong to different Google accounts."""


class AccountVerificationError(TasksError):
    """The available API data could not establish account identity."""


class NativeTaskUnavailable(TasksError):
    """A single task cannot be joined to a native record; reason contains no user data."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(
            "This task's native schedule is unavailable. Its time cannot be edited until verified."
        )


class OutcomeUnknown(TasksError):
    """A write may have landed; never replay it automatically."""

    def __init__(self):
        super().__init__("The change could not be confirmed. Refresh the list before retrying.")


class PartialChange(TasksError):
    """Content/creation succeeded but a separate schedule mutation did not."""
