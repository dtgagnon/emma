"""TODO.json file manager for persisting action items."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default schema for a new TODO.json
_DEFAULT_TODO: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "version": "1.0.0",
    "tasks": [],
    "meta": {
        "statuses": ["pending", "in_progress", "completed", "cancelled"],
        "priorities": ["low", "medium", "high", "urgent"],
        "reserved_tags": ["delegate"],
        "rules": [],
    },
}


class TodoFileManager:
    """Reads and writes action items to a TODO.json file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        """Read and parse TODO.json, creating it if it doesn't exist."""
        if not self.path.exists():
            return json.loads(json.dumps(_DEFAULT_TODO))
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read {self.path}: {e}")
            return json.loads(json.dumps(_DEFAULT_TODO))

    def _write(self, data: dict[str, Any]) -> None:
        """Write data back to TODO.json."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str) + "\n")

    def _next_id(self, tasks: list[dict[str, Any]]) -> str:
        """Determine the next sequential ID (zero-padded 3 digits)."""
        max_id = 0
        for task in tasks:
            try:
                max_id = max(max_id, int(task["id"]))
            except (ValueError, KeyError):
                continue
        return f"{max_id + 1:03d}"

    def add_item(
        self,
        title: str,
        *,
        description: str | None = None,
        priority: str = "medium",
        due_date: datetime | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add an action item to TODO.json.

        Args:
            title: Task title.
            description: Optional description.
            priority: Priority level (low/medium/high/urgent).
            due_date: Optional due date.
            tags: Optional tags (emma source tag added automatically).
            metadata: Optional extra metadata (email_subject, email_from, etc.).

        Returns:
            The assigned task ID.
        """
        data = self._read()
        tasks = data.get("tasks", [])
        task_id = self._next_id(tasks)

        task_tags = ["emma"]
        if tags:
            task_tags.extend(t for t in tags if t not in task_tags)

        task: dict[str, Any] = {
            "id": task_id,
            "title": title,
            "status": "pending",
            "priority": priority,
            "tags": task_tags,
            "created": datetime.now().isoformat() + "Z",
        }

        if description:
            task["description"] = description
        if due_date:
            task["due"] = due_date.isoformat() + "Z"
        if metadata:
            task["source"] = metadata

        tasks.append(task)
        data["tasks"] = tasks
        self._write(data)

        logger.info(f"Added task {task_id} to {self.path}: {title}")
        return task_id

    def update_status(self, task_id: str, status: str) -> bool:
        """Update the status of a task in TODO.json.

        Args:
            task_id: The task ID to update.
            status: New status value.

        Returns:
            True if the task was found and updated.
        """
        data = self._read()
        for task in data.get("tasks", []):
            if task.get("id") == task_id:
                task["status"] = status
                if status == "completed":
                    task["completed"] = datetime.now().isoformat() + "Z"
                self._write(data)
                logger.info(f"Updated task {task_id} status to {status}")
                return True
        return False

    def find_by_source_email(self, email_subject: str, email_from: str) -> dict[str, Any] | None:
        """Find a task by its source email metadata.

        Args:
            email_subject: The original email subject.
            email_from: The original email sender.

        Returns:
            The task dict if found, None otherwise.
        """
        data = self._read()
        for task in data.get("tasks", []):
            source = task.get("source", {})
            if (
                source.get("email_subject") == email_subject
                and source.get("email_from") == email_from
            ):
                return task
        return None
