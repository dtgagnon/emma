"""TODO.json file manager for persisting action items.

Writes entries in Todoist export schema (matching `~/TODO.json`'s native format)
so the simple-todo TUI parses the file cleanly. Keys/fields used:
- `content`          task name (NOT `title`)
- `priority`         integer 1..4 (low=1, medium=2, high=3, urgent=4)
- `labels`           list[str] (NOT `tags`)
- `due`              DueDate object: {date, datetime, string, timezone, is_recurring, lang}
- `created_at`       ISO timestamp (NOT `created`)
- `completed_at`     ISO timestamp on completion (NOT `completed`)
- `is_completed`     bool
- `status`           free-form string, retained for emma's own filtering
- `meta`             dict for source email info (NOT `source`)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default schema for a new TODO.json (Todoist export shape).
_DEFAULT_TODO: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "version": "2.0.0",
    "projects": [],
    "sections": [],
    "comments": [],
    "tasks": [],
    "meta": {
        "statuses": ["pending", "in_progress", "completed", "cancelled"],
        "priority_scale": {"1": "low", "2": "medium", "3": "high", "4": "urgent"},
        "reserved_labels": ["emma", "email"],
    },
    "labels": [],
}

_PRIORITY_STR_TO_INT: dict[str, int] = {
    "low": 1,
    "medium": 2,
    "normal": 2,
    "high": 3,
    "urgent": 4,
}


def _priority_to_int(priority: str | int) -> int:
    if isinstance(priority, int):
        return priority if 1 <= priority <= 4 else 2
    return _PRIORITY_STR_TO_INT.get(str(priority).lower(), 2)


def _due_to_object(due_date: datetime) -> dict[str, Any]:
    """Convert a datetime to a Todoist DueDate object."""
    return {
        "date": due_date.date().isoformat(),
        "datetime": due_date.isoformat() + ("Z" if due_date.tzinfo is None else ""),
        "string": None,
        "timezone": None,
        "is_recurring": False,
        "lang": "en",
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
            except (ValueError, KeyError, TypeError):
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
        """Add an action item to TODO.json (Todoist schema).

        Args:
            title: Task content (maps to Todoist `content`).
            description: Optional description.
            priority: Priority name (low/medium/high/urgent) — converted to int 1..4.
            due_date: Optional due date.
            tags: Optional labels (`emma` is added automatically).
            metadata: Optional source info (email_subject, email_from, etc.); stored under `meta`.

        Returns:
            The assigned task ID.
        """
        data = self._read()
        tasks = data.get("tasks", [])
        task_id = self._next_id(tasks)

        labels = ["emma"]
        if tags:
            labels.extend(t for t in tags if t not in labels)

        now = datetime.now().isoformat() + "Z"

        task: dict[str, Any] = {
            "id": task_id,
            "content": title,
            "description": description or "",
            "project_id": None,
            "section_id": None,
            "parent_id": None,
            "order": len(tasks) + 1,
            "labels": labels,
            "priority": _priority_to_int(priority),
            "due": _due_to_object(due_date) if due_date else None,
            "deadline": None,
            "duration": None,
            "is_completed": False,
            "is_collapsed": False,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "comment_count": 0,
        }

        if metadata:
            task["meta"] = metadata

        tasks.append(task)
        data["tasks"] = tasks
        self._write(data)

        logger.info(f"Added task {task_id} to {self.path}: {title}")
        return task_id

    def update_status(self, task_id: str, status: str) -> bool:
        """Update the status of a task in TODO.json.

        Sets `status`, `updated_at`, and on completion also `is_completed` and `completed_at`.
        """
        data = self._read()
        for task in data.get("tasks", []):
            if task.get("id") == task_id:
                now = datetime.now().isoformat() + "Z"
                task["status"] = status
                task["updated_at"] = now
                if status == "completed":
                    task["is_completed"] = True
                    task["completed_at"] = now
                self._write(data)
                logger.info(f"Updated task {task_id} status to {status}")
                return True
        return False

    def find_by_source_email(self, email_subject: str, email_from: str) -> dict[str, Any] | None:
        """Find a task by its source email metadata.

        Looks under the Todoist-style `meta` key first, falling back to the
        legacy `source` key for any pre-migration entries.
        """
        data = self._read()
        for task in data.get("tasks", []):
            meta = task.get("meta") or task.get("source") or {}
            if (
                meta.get("email_subject") == email_subject
                and meta.get("email_from") == email_from
            ):
                return task
        return None
