"""Provider failures with messages that contain no provider response or credentials."""

from homeassistant.exceptions import HomeAssistantError


class TasksError(HomeAssistantError):
    """A safe API or schema failure."""


class NativeAuthError(TasksError):
    """Native credentials were rejected."""


class WrongAccount(TasksError):
    """OAuth and native credentials belong to different Google accounts."""


class OutcomeUnknown(TasksError):
    """A write may have landed; never replay it automatically."""

    def __init__(self):
        super().__init__("The change could not be confirmed. Refresh the list before retrying.")


class PartialChange(TasksError):
    """Content/creation succeeded but a separate schedule mutation did not."""
