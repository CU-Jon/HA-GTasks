"""Google Tasks todo platform."""

from datetime import datetime
from typing import Any, cast, override

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GoogleTasksConfigEntry, TaskUpdateCoordinator

PARALLEL_UPDATES = 0

TODO_STATUS_MAP = {
    "needsAction": TodoItemStatus.NEEDS_ACTION,
    "completed": TodoItemStatus.COMPLETED,
}
TODO_STATUS_MAP_INV = {v: k for k, v in TODO_STATUS_MAP.items()}


def _convert_todo_item(item: TodoItem) -> dict[str, str | None]:
    """Convert TodoItem dataclass items to dictionary of attributes the tasks API."""
    result: dict[str, str | None] = {}
    result["title"] = item.summary
    if item.status is not None:
        result["status"] = TODO_STATUS_MAP_INV[item.status]
    else:
        result["status"] = TodoItemStatus.NEEDS_ACTION
    result["notes"] = item.description
    return result


def _convert_api_item(item: dict[str, Any]) -> TodoItem:
    """Convert tasks API items into a TodoItem."""
    completed: datetime | None = None
    if (completed_str := item.get("completed")) is not None:
        completed = datetime.fromisoformat(completed_str)
    return TodoItem(
        summary=item.get("title", ""),
        uid=item["id"],
        status=TODO_STATUS_MAP.get(
            item.get("status", ""),
            TodoItemStatus.NEEDS_ACTION,
        ),
        due=item["_native_due"],
        completed=completed,
        description=item.get("notes"),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoogleTasksConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Google Tasks todo platform."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_lists():
        entities = []
        for task_list in coordinator.data["lists"]:
            if task_list["id"] not in known:
                known.add(task_list["id"])
                entities.append(
                    GoogleTaskTodoListEntity(
                        coordinator, task_list["title"], entry.entry_id, task_list["id"]
                    )
                )
        if entities:
            async_add_entities(entities)

    add_new_lists()
    entry.async_on_unload(coordinator.async_add_listener(add_new_lists))


class GoogleTaskTodoListEntity(CoordinatorEntity[TaskUpdateCoordinator], TodoListEntity):
    """A Google task list with date and date-time support."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(
        self,
        coordinator: TaskUpdateCoordinator,
        name: str,
        config_entry_id: str,
        task_list_id: str,
    ) -> None:
        """Initialize GoogleTaskTodoListEntity."""
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{config_entry_id}-{task_list_id}"
        self._task_list_id = task_list_id

    @property
    @override
    def todo_items(self) -> list[TodoItem] | None:
        """Get the current set of To-do items."""
        return [
            _convert_api_item(item)
            for item in _order_tasks(self.coordinator.data["tasks"].get(self._task_list_id, []))
        ]

    @property
    def available(self) -> bool:
        return super().available and self._task_list_id in self.coordinator.data["tasks"]

    @property
    def extra_state_attributes(self):
        return {
            "tasks_with_unverified_times": sum(
                item.get("_native_schedule_available") is False
                for item in self.coordinator.data["tasks"].get(self._task_list_id, [])
            )
        }

    @override
    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add an item to the To-do list."""
        try:
            await self.coordinator.api.insert(
                self._task_list_id,
                task=_convert_todo_item(item),
                due=item.due,
            )
        finally:
            await self.coordinator.async_refresh()

    @override
    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Update a To-do item."""
        uid: str = cast(str, item.uid)
        try:
            await self.coordinator.api.patch(
                self._task_list_id,
                uid,
                task=_convert_todo_item(item),
                due=item.due,
            )
        finally:
            await self.coordinator.async_refresh()

    @override
    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete To-do items."""
        try:
            await self.coordinator.api.delete(self._task_list_id, uids)
        finally:
            await self.coordinator.async_refresh()

    @override
    async def async_move_todo_item(self, uid: str, previous_uid: str | None = None) -> None:
        """Re-order a To-do item."""
        try:
            await self.coordinator.api.move(self._task_list_id, uid, previous=previous_uid)
        finally:
            await self.coordinator.async_refresh()


def _order_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order the task items response.

    All tasks have an order amongst their siblings based on position.

    Home Assistant To-do items do not support the Google Task parent/sibling
    relationships and the desired behavior is for them to be filtered.
    """
    parents = [task for task in tasks if task.get("parent") is None]
    parents.sort(key=lambda task: task["position"])
    return parents
