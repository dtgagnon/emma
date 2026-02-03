"""Service state management with SQLite persistence."""

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from ..models import (
    ActionItem,
    ActionItemStatus,
    Digest,
    DigestStatus,
    EmailPriority,
    ProcessedEmail,
)


def _generate_email_hash(email_id: str, source: str, folder: str, message_id: str | None = None) -> str:
    """Generate a unique hash for an email.

    Uses message_id if available, otherwise uses source:folder:email_id.
    """
    if message_id:
        data = message_id
    else:
        data = f"{source}:{folder}:{email_id}"
    return hashlib.sha256(data.encode()).hexdigest()


class ServiceState:
    """Manages service state with SQLite persistence."""

    def __init__(self, db_path: Path) -> None:
        """Initialize the service state manager.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Ensure the database and tables exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            # Processed emails table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_emails (
                    id TEXT PRIMARY KEY,
                    message_id TEXT,
                    email_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    digest_id TEXT,
                    classification TEXT,
                    llm_analysis TEXT,
                    subject TEXT,
                    from_addr TEXT,
                    date TEXT
                )
            """)

            # Migrate existing tables to add new columns
            self._migrate_processed_emails(conn)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed_timestamp
                ON processed_emails (processed_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed_source
                ON processed_emails (source, folder)
            """)

            # Digests table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS digests (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    email_count INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    raw_content TEXT,
                    delivery_status TEXT DEFAULT 'pending'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_digest_created
                ON digests (created_at DESC)
            """)

            # Action items table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_items (
                    id TEXT PRIMARY KEY,
                    email_id TEXT NOT NULL,
                    digest_id TEXT,
                    created_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    priority TEXT DEFAULT 'normal',
                    urgency TEXT DEFAULT 'normal',
                    due_date TEXT,
                    status TEXT DEFAULT 'pending',
                    completed_at TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_action_status
                ON action_items (status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_action_due
                ON action_items (due_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_action_email
                ON action_items (email_id)
            """)

            conn.commit()

    def _migrate_processed_emails(self, conn: sqlite3.Connection) -> None:
        """Add new columns to existing processed_emails table if needed."""
        cursor = conn.execute("PRAGMA table_info(processed_emails)")
        columns = {row[1] for row in cursor.fetchall()}

        migrations = [
            ("subject", "TEXT"),
            ("from_addr", "TEXT"),
            ("date", "TEXT"),
        ]

        for col_name, col_type in migrations:
            if col_name not in columns:
                conn.execute(f"ALTER TABLE processed_emails ADD COLUMN {col_name} {col_type}")
                conn.commit()

    # ========== Processed Emails ==========

    def is_email_processed(
        self,
        email_id: str,
        source: str,
        folder: str,
        message_id: str | None = None,
    ) -> bool:
        """Check if an email has already been processed.

        Args:
            email_id: The email's unique ID within its source.
            source: The email source name.
            folder: The folder containing the email.
            message_id: The email's Message-ID header if available.

        Returns:
            True if the email has been processed, False otherwise.
        """
        hash_id = _generate_email_hash(email_id, source, folder, message_id)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT 1 FROM processed_emails WHERE id = ?",
                (hash_id,),
            )
            return cursor.fetchone() is not None

    def mark_email_processed(
        self,
        email_id: str,
        source: str,
        folder: str,
        message_id: str | None = None,
        classification: dict | None = None,
        llm_analysis: dict | None = None,
        digest_id: str | None = None,
        subject: str | None = None,
        from_addr: str | None = None,
        date: datetime | None = None,
    ) -> ProcessedEmail:
        """Mark an email as processed.

        Args:
            email_id: The email's unique ID within its source.
            source: The email source name.
            folder: The folder containing the email.
            message_id: The email's Message-ID header if available.
            classification: Classification result (category, priority).
            llm_analysis: Full LLM analysis blob.
            digest_id: ID of the digest this email was included in.
            subject: Email subject line.
            from_addr: Email sender address.
            date: Email date.

        Returns:
            The created ProcessedEmail record.
        """
        hash_id = _generate_email_hash(email_id, source, folder, message_id)
        processed = ProcessedEmail(
            id=hash_id,
            message_id=message_id,
            email_id=email_id,
            source=source,
            folder=folder,
            processed_at=datetime.now(),
            digest_id=digest_id,
            classification=classification,
            llm_analysis=llm_analysis,
            subject=subject,
            from_addr=from_addr,
            date=date,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_emails (
                    id, message_id, email_id, source, folder, processed_at,
                    digest_id, classification, llm_analysis, subject, from_addr, date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    processed.id,
                    processed.message_id,
                    processed.email_id,
                    processed.source,
                    processed.folder,
                    processed.processed_at.isoformat(),
                    processed.digest_id,
                    json.dumps(processed.classification) if processed.classification else None,
                    json.dumps(processed.llm_analysis) if processed.llm_analysis else None,
                    processed.subject,
                    processed.from_addr,
                    processed.date.isoformat() if processed.date else None,
                ),
            )
            conn.commit()

        return processed

    def get_processed_emails(
        self,
        *,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[ProcessedEmail]:
        """Get processed emails with optional filters.

        Args:
            source: Filter by source name.
            since: Only return emails processed after this time.
            until: Only return emails processed before this time.
            limit: Maximum number of emails to return.

        Returns:
            List of ProcessedEmail records, newest first.
        """
        query = "SELECT * FROM processed_emails WHERE 1=1"
        params: list = []

        if source:
            query += " AND source = ?"
            params.append(source)

        if since:
            query += " AND processed_at >= ?"
            params.append(since.isoformat())

        if until:
            query += " AND processed_at <= ?"
            params.append(until.isoformat())

        query += " ORDER BY processed_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [self._row_to_processed_email(row) for row in cursor.fetchall()]

    def get_undigested_emails(self, since: datetime) -> list[ProcessedEmail]:
        """Get processed emails not yet included in a digest.

        Args:
            since: Only return emails processed after this time.

        Returns:
            List of ProcessedEmail records without a digest_id.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM processed_emails
                WHERE digest_id IS NULL AND processed_at >= ?
                ORDER BY processed_at ASC
                """,
                (since.isoformat(),),
            )
            return [self._row_to_processed_email(row) for row in cursor.fetchall()]

    def update_email_digest_id(self, email_hash_id: str, digest_id: str) -> None:
        """Update the digest_id for a processed email.

        Args:
            email_hash_id: The processed email's hash ID.
            digest_id: The digest ID to associate.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE processed_emails SET digest_id = ? WHERE id = ?",
                (digest_id, email_hash_id),
            )
            conn.commit()

    # ========== Digests ==========

    def create_digest(
        self,
        period_start: datetime,
        period_end: datetime,
        email_count: int,
        summary: str,
        raw_content: str | None = None,
    ) -> Digest:
        """Create a new digest record.

        Args:
            period_start: Start of the digest period.
            period_end: End of the digest period.
            email_count: Number of emails in the digest.
            summary: Executive summary of the digest.
            raw_content: Full markdown content of the digest.

        Returns:
            The created Digest record.
        """
        digest = Digest(
            id=str(uuid.uuid4()),
            created_at=datetime.now(),
            period_start=period_start,
            period_end=period_end,
            email_count=email_count,
            summary=summary,
            raw_content=raw_content,
            delivery_status=DigestStatus.PENDING,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO digests (
                    id, created_at, period_start, period_end,
                    email_count, summary, raw_content, delivery_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest.id,
                    digest.created_at.isoformat(),
                    digest.period_start.isoformat(),
                    digest.period_end.isoformat(),
                    digest.email_count,
                    digest.summary,
                    digest.raw_content,
                    digest.delivery_status.value,
                ),
            )
            conn.commit()

        return digest

    def get_digest(self, digest_id: str) -> Digest | None:
        """Get a specific digest by ID.

        Args:
            digest_id: The UUID of the digest.

        Returns:
            The Digest if found, None otherwise.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM digests WHERE id = ?",
                (digest_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_digest(row)
        return None

    def list_digests(self, *, limit: int = 10) -> list[Digest]:
        """List recent digests.

        Args:
            limit: Maximum number of digests to return.

        Returns:
            List of Digest records, newest first.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM digests ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [self._row_to_digest(row) for row in cursor.fetchall()]

    def update_digest_status(self, digest_id: str, status: DigestStatus) -> bool:
        """Update the delivery status of a digest.

        Args:
            digest_id: The UUID of the digest.
            status: The new delivery status.

        Returns:
            True if the digest was updated, False if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE digests SET delivery_status = ? WHERE id = ?",
                (status.value, digest_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ========== Action Items ==========

    def create_action_item(
        self,
        email_id: str,
        title: str,
        *,
        description: str | None = None,
        priority: EmailPriority = EmailPriority.NORMAL,
        urgency: str = "normal",
        due_date: datetime | None = None,
        digest_id: str | None = None,
        metadata: dict | None = None,
    ) -> ActionItem:
        """Create a new action item.

        Args:
            email_id: The processed email hash ID this action came from.
            title: The action item title.
            description: Optional detailed description.
            priority: Priority level.
            urgency: Urgency level (low/normal/high/urgent).
            due_date: Optional due date.
            digest_id: Optional digest ID if created during digest.
            metadata: Optional additional metadata.

        Returns:
            The created ActionItem record.
        """
        item = ActionItem(
            id=str(uuid.uuid4()),
            email_id=email_id,
            digest_id=digest_id,
            created_at=datetime.now(),
            title=title,
            description=description,
            priority=priority,
            urgency=urgency,
            due_date=due_date,
            status=ActionItemStatus.PENDING,
            metadata=metadata or {},
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO action_items (
                    id, email_id, digest_id, created_at, title, description,
                    priority, urgency, due_date, status, completed_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.email_id,
                    item.digest_id,
                    item.created_at.isoformat(),
                    item.title,
                    item.description,
                    item.priority.value,
                    item.urgency,
                    item.due_date.isoformat() if item.due_date else None,
                    item.status.value,
                    None,
                    json.dumps(item.metadata),
                ),
            )
            conn.commit()

        return item

    def get_action_item(self, item_id: str) -> ActionItem | None:
        """Get a specific action item by ID.

        Args:
            item_id: The UUID of the action item.

        Returns:
            The ActionItem if found, None otherwise.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM action_items WHERE id = ?",
                (item_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_action_item(row)
        return None

    def list_action_items(
        self,
        *,
        status: ActionItemStatus | None = None,
        priority: EmailPriority | None = None,
        email_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionItem]:
        """List action items with optional filters.

        Args:
            status: Filter by status.
            priority: Filter by priority.
            email_id: Filter by email.
            limit: Maximum number of items to return.

        Returns:
            List of ActionItem records.
        """
        query = "SELECT * FROM action_items WHERE 1=1"
        params: list = []

        if status:
            query += " AND status = ?"
            params.append(status.value)

        if priority:
            query += " AND priority = ?"
            params.append(priority.value)

        if email_id:
            query += " AND email_id = ?"
            params.append(email_id)

        query += " ORDER BY due_date ASC NULLS LAST, created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [self._row_to_action_item(row) for row in cursor.fetchall()]

    def update_action_status(
        self,
        item_id: str,
        status: ActionItemStatus,
    ) -> bool:
        """Update the status of an action item.

        Args:
            item_id: The UUID of the action item.
            status: The new status.

        Returns:
            True if the item was updated, False if not found.
        """
        completed_at = datetime.now().isoformat() if status == ActionItemStatus.COMPLETED else None

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE action_items
                SET status = ?, completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (status.value, completed_at, item_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ========== Cleanup ==========

    def cleanup_old_data(self, days: int = 30) -> dict[str, int]:
        """Remove data older than the specified number of days.

        Args:
            days: Number of days to retain data.

        Returns:
            Dict with counts of deleted items by table.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        deleted = {}

        with sqlite3.connect(self.db_path) as conn:
            # Clean old processed emails
            cursor = conn.execute(
                "DELETE FROM processed_emails WHERE processed_at < ?",
                (cutoff,),
            )
            deleted["processed_emails"] = cursor.rowcount

            # Clean old digests
            cursor = conn.execute(
                "DELETE FROM digests WHERE created_at < ?",
                (cutoff,),
            )
            deleted["digests"] = cursor.rowcount

            # Clean completed/dismissed action items
            cursor = conn.execute(
                """
                DELETE FROM action_items
                WHERE status IN ('completed', 'dismissed') AND created_at < ?
                """,
                (cutoff,),
            )
            deleted["action_items"] = cursor.rowcount

            conn.commit()

        return deleted

    def get_stats(self) -> dict:
        """Get service statistics.

        Returns:
            Dict with counts and recent activity info.
        """
        with sqlite3.connect(self.db_path) as conn:
            stats = {}

            # Total counts
            cursor = conn.execute("SELECT COUNT(*) FROM processed_emails")
            stats["total_processed_emails"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM digests")
            stats["total_digests"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM action_items")
            stats["total_action_items"] = cursor.fetchone()[0]

            # Action items by status
            cursor = conn.execute(
                """
                SELECT status, COUNT(*) FROM action_items
                GROUP BY status
                """
            )
            stats["action_items_by_status"] = dict(cursor.fetchall())

            # Recent activity (last 24h)
            yesterday = (datetime.now() - timedelta(days=1)).isoformat()
            cursor = conn.execute(
                "SELECT COUNT(*) FROM processed_emails WHERE processed_at >= ?",
                (yesterday,),
            )
            stats["emails_last_24h"] = cursor.fetchone()[0]

            # Last digest
            cursor = conn.execute(
                "SELECT created_at FROM digests ORDER BY created_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            stats["last_digest"] = row[0] if row else None

            return stats

    # ========== Row Converters ==========

    def _row_to_processed_email(self, row: sqlite3.Row) -> ProcessedEmail:
        """Convert a database row to a ProcessedEmail."""
        return ProcessedEmail(
            id=row["id"],
            message_id=row["message_id"],
            email_id=row["email_id"],
            source=row["source"],
            folder=row["folder"],
            processed_at=datetime.fromisoformat(row["processed_at"]),
            digest_id=row["digest_id"],
            classification=json.loads(row["classification"]) if row["classification"] else None,
            llm_analysis=json.loads(row["llm_analysis"]) if row["llm_analysis"] else None,
            subject=row["subject"],
            from_addr=row["from_addr"],
            date=datetime.fromisoformat(row["date"]) if row["date"] else None,
        )

    def _row_to_digest(self, row: sqlite3.Row) -> Digest:
        """Convert a database row to a Digest."""
        return Digest(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            period_start=datetime.fromisoformat(row["period_start"]),
            period_end=datetime.fromisoformat(row["period_end"]),
            email_count=row["email_count"],
            summary=row["summary"],
            raw_content=row["raw_content"],
            delivery_status=DigestStatus(row["delivery_status"]),
        )

    def _row_to_action_item(self, row: sqlite3.Row) -> ActionItem:
        """Convert a database row to an ActionItem."""
        return ActionItem(
            id=row["id"],
            email_id=row["email_id"],
            digest_id=row["digest_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            title=row["title"],
            description=row["description"],
            priority=EmailPriority(row["priority"]),
            urgency=row["urgency"],
            due_date=datetime.fromisoformat(row["due_date"]) if row["due_date"] else None,
            status=ActionItemStatus(row["status"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
