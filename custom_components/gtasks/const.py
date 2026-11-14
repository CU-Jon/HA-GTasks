"""Constants for Google Tasks with Times."""

from enum import StrEnum

DOMAIN = "gtasks"
CONF_MASTER_TOKEN = "master_token"
CONF_DEVICE_ID = "device_id"
CONF_ACCOUNT_ID = "account_id"
CONF_COMPLETED_TASK_LIMIT = "completed_task_limit"
DEFAULT_COMPLETED_TASK_LIMIT = 100
MAX_COMPLETED_TASK_LIMIT = 500
OAUTH2_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN = "https://oauth2.googleapis.com/token"
OAUTH2_SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
]
TASKS_URL = "https://tasks.googleapis.com/tasks/v1"
SYNC_URL = "https://tasks-pa.clients6.google.com/$rpc/google.internal.tasks.v1.TasksApiService/Sync"
TASKS_APP = "com.google.android.apps.tasks"
TASKS_CERT = "5af12649728293f2d9f5e8c61e455a8fea16e7d8"
TASKS_SCOPE = "oauth2:https://www.googleapis.com/auth/tasks"
NETWORK_TIMEOUT = 20


class TaskStatus(StrEnum):
    """Public Tasks completion states."""

    NEEDS_ACTION = "needsAction"
    COMPLETED = "completed"
