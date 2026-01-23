"""Audit logging system for tracking email operations."""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Literal

from .models import ActionType, AuditEntry


class AuditLogger:
    """Manages audit logging with SQLite persistence."""

    def __init__(self, db_path: Path) -> None:
        """Initialize the audit logger with the given database path.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Ensure the database and table exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    email_id TEXT NOT NULL,
                    email_subject TEXT NOT NULL,
                    rule_name TEXT,
                    source_folder TEXT,
                    target_folder TEXT,
                    details TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp
                ON audit_log (timestamp DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_email_id
                ON audit_log (email_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_action_type
                ON audit_log (action_type)
            """)
            conn.commit()

    def log_action(
        self,
        action_type: ActionType,
        email_id: str,
        email_subject: str,
        *,
        rule_name: str | None = None,
        source_folder: str | None = None,
        target_folder: str | None = None,
        details: dict | None = None,
        dry_run: bool = False,
    ) -> AuditEntry:
        """Log an action to the audit trail.

        Args:
            action_type: The type of action being performed.
            email_id: The ID of the email being acted upon.
            email_subject: The subject of the email (for readability).
            rule_name: The name of the rule that triggered this action.
            source_folder: The source folder (for move operations).
            target_folder: The target folder (for move operations).
            details: Additional context as a dictionary.
            dry_run: Whether this was a preview (not actually executed).

        Returns:
            The created AuditEntry.
        """
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            action_type=action_type,
            email_id=email_id,
            email_subject=email_subject,
            rule_name=rule_name,
            source_folder=source_folder,
            target_folder=target_folder,
            details=details or {},
            dry_run=dry_run,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO audit_log (
                    id, timestamp, action_type, email_id, email_subject,
                    rule_name, source_folder, target_folder, details, dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.timestamp.isoformat(),
                    entry.action_type.value,
                    entry.email_id,
                    entry.email_subject,
                    entry.rule_name,
                    entry.source_folder,
                    entry.target_folder,
                    json.dumps(entry.details),
                    1 if entry.dry_run else 0,
                ),
            )
            conn.commit()

        return entry

    def get_entry(self, entry_id: str) -> AuditEntry | None:
        """Get a specific audit entry by ID.

        Args:
            entry_id: The UUID of the audit entry.

        Returns:
            The AuditEntry if found, None otherwise.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM audit_log WHERE id = ?",
                (entry_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_entry(row)
        return None

    def get_history(
        self,
        *,
        email_id: str | None = None,
        action_type: ActionType | None = None,
        since: datetime | None = None,
        limit: int = 100,
        include_dry_run: bool = False,
    ) -> list[AuditEntry]:
        """Get audit history with optional filters.

        Args:
            email_id: Filter by specific email ID.
            action_type: Filter by action type.
            since: Only return entries after this timestamp.
            limit: Maximum number of entries to return.
            include_dry_run: Whether to include dry-run entries.

        Returns:
            List of matching AuditEntry objects, newest first.
        """
        query = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []

        if email_id:
            query += " AND email_id = ?"
            params.append(email_id)

        if action_type:
            query += " AND action_type = ?"
            params.append(action_type.value)

        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        if not include_dry_run:
            query += " AND dry_run = 0"

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def iter_all(self, *, include_dry_run: bool = False) -> Iterator[AuditEntry]:
        """Iterate over all audit entries.

        Args:
            include_dry_run: Whether to include dry-run entries.

        Yields:
            AuditEntry objects, newest first.
        """
        query = "SELECT * FROM audit_log"
        if not include_dry_run:
            query += " WHERE dry_run = 0"
        query += " ORDER BY timestamp DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query)
            for row in cursor:
                yield self._row_to_entry(row)

    def export_log(
        self,
        format: Literal["json", "csv"] = "json",
        *,
        include_dry_run: bool = False,
    ) -> str:
        """Export the audit log in the specified format.

        Args:
            format: Output format ('json' or 'csv').
            include_dry_run: Whether to include dry-run entries.

        Returns:
            The formatted audit log as a string.
        """
        entries = list(self.iter_all(include_dry_run=include_dry_run))

        if format == "json":
            return json.dumps(
                [entry.model_dump(mode="json") for entry in entries],
                indent=2,
                default=str,
            )

        # CSV format
        import csv
        import io

        output = io.StringIO()
        if entries:
            fieldnames = [
                "id",
                "timestamp",
                "action_type",
                "email_id",
                "email_subject",
                "rule_name",
                "source_folder",
                "target_folder",
                "details",
                "dry_run",
            ]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for entry in entries:
                row = entry.model_dump(mode="json")
                row["details"] = json.dumps(row["details"])
                writer.writerow(row)
        return output.getvalue()

    def clear(self, *, before: datetime | None = None) -> int:
        """Clear audit entries.

        Args:
            before: If provided, only clear entries before this timestamp.
                   If None, clears all entries.

        Returns:
            The number of entries deleted.
        """
        with sqlite3.connect(self.db_path) as conn:
            if before:
                cursor = conn.execute(
                    "DELETE FROM audit_log WHERE timestamp < ?",
                    (before.isoformat(),),
                )
            else:
                cursor = conn.execute("DELETE FROM audit_log")
            conn.commit()
            return cursor.rowcount

    def _row_to_entry(self, row: sqlite3.Row) -> AuditEntry:
        """Convert a database row to an AuditEntry."""
        return AuditEntry(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            action_type=ActionType(row["action_type"]),
            email_id=row["email_id"],
            email_subject=row["email_subject"],
            rule_name=row["rule_name"],
            source_folder=row["source_folder"],
            target_folder=row["target_folder"],
            details=json.loads(row["details"]),
            dry_run=bool(row["dry_run"]),
        )
