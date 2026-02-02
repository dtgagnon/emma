"""Notmuch email source connector.

Uses notmuch CLI for searching and reading emails from local Maildir storage.
This is the preferred source for emma as it leverages notmuch's indexing
and search capabilities.
"""

import json
import subprocess
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from email_agent.models import Attachment, Email
from email_agent.utils.text import html_to_text

from .base import EmailSource


def _date_query(days: int | None = None, hours: int | None = None) -> str:
    """Build a reliable notmuch date query using explicit timestamps.

    Notmuch's relative date queries (1week.., 1month..) can be unreliable.
    This function calculates an explicit date range.

    Args:
        days: Number of days to look back
        hours: Number of hours to look back (overrides days if both given)

    Returns:
        Notmuch date query string like "date:2026-01-25.."
    """
    if hours is not None:
        since = datetime.now() - timedelta(hours=hours)
    elif days is not None:
        since = datetime.now() - timedelta(days=days)
    else:
        # Default to 7 days
        since = datetime.now() - timedelta(days=7)

    # Format as YYYY-MM-DD which notmuch reliably understands
    return f"date:{since.strftime('%Y-%m-%d')}.."


class NotmuchError(Exception):
    """Error from notmuch command execution."""

    pass


class NotmuchSource(EmailSource):
    """Email source using notmuch for search and retrieval.

    Unlike IMAP or raw Maildir sources, NotmuchSource leverages notmuch's
    powerful search capabilities and tagging system.

    Key features:
    - Query-based email fetching using notmuch query syntax
    - Tag-based processing state tracking
    - Efficient indexed search across all accounts
    """

    def __init__(
        self,
        name: str = "notmuch",
        processed_tag: str = "emma-processed",
        database_path: str | None = None,
    ) -> None:
        """Initialize NotmuchSource.

        Args:
            name: Source identifier
            processed_tag: Tag to apply to processed emails
            database_path: Path to notmuch database (uses default if None)
        """
        self.name = name
        self.processed_tag = processed_tag
        self.database_path = database_path
        self._connected = False

    def _run_notmuch(
        self, args: list[str], check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run a notmuch command.

        Args:
            args: Command arguments (without 'notmuch' prefix)
            check: Raise on non-zero exit code

        Returns:
            CompletedProcess with stdout/stderr

        Raises:
            NotmuchError: If command fails and check=True
        """
        cmd = ["notmuch"]
        if self.database_path:
            cmd.extend(["--config", self.database_path])
        cmd.extend(args)

        result = subprocess.run(cmd, capture_output=True, text=True)

        if check and result.returncode != 0:
            raise NotmuchError(f"notmuch {args[0]} failed: {result.stderr}")

        return result

    async def connect(self) -> None:
        """Verify notmuch is available and database exists."""
        try:
            result = self._run_notmuch(["count", "*"])
            self._connected = True
        except FileNotFoundError:
            raise NotmuchError("notmuch not found in PATH")
        except NotmuchError as e:
            raise NotmuchError(f"Failed to connect to notmuch: {e}")

    async def disconnect(self) -> None:
        """No-op for notmuch (local database)."""
        self._connected = False

    async def list_folders(self) -> list[str]:
        """List available folders based on path structure.

        Returns unique folder names from the notmuch database.
        """
        result = self._run_notmuch(
            ["search", "--output=files", "--format=text", "*"]
        )

        folders = set()
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            # Extract folder from path: ~/Mail/account/FOLDER/cur/file
            parts = line.split("/")
            for i, part in enumerate(parts):
                if part in ("cur", "new", "tmp") and i > 0:
                    folders.add(parts[i - 1])
                    break

        return sorted(folders)

    async def list_tags(self) -> list[str]:
        """List all available tags in the database."""
        result = self._run_notmuch(["search", "--output=tags", "*"])
        return [t for t in result.stdout.strip().split("\n") if t]

    async def search(self, query: str, limit: int | None = None) -> list[str]:
        """Search for message IDs matching a query.

        Args:
            query: Notmuch query string
            limit: Maximum results to return

        Returns:
            List of message IDs
        """
        args = ["search", "--output=messages", "--format=text", query]
        result = self._run_notmuch(args)

        message_ids = [
            mid for mid in result.stdout.strip().split("\n") if mid
        ]

        if limit:
            message_ids = message_ids[:limit]

        return message_ids

    async def count(self, query: str) -> int:
        """Count messages matching a query."""
        result = self._run_notmuch(["count", query])
        return int(result.stdout.strip())

    async def fetch_recent(
        self,
        days: int | None = None,
        hours: int | None = None,
        limit: int | None = None,
        additional_query: str | None = None,
    ) -> AsyncIterator[Email]:
        """Fetch recent emails using explicit date ranges.

        This is more reliable than notmuch's relative date syntax.

        Args:
            days: Number of days to look back (default: 7)
            hours: Number of hours to look back (overrides days)
            limit: Maximum emails to fetch
            additional_query: Extra query terms to add

        Yields:
            Email objects from the specified time period
        """
        query_parts = [_date_query(days=days, hours=hours)]

        if additional_query:
            query_parts.append(additional_query)

        query = " AND ".join(query_parts)
        async for email in self.fetch_by_query(query, limit=limit):
            yield email

    async def fetch_emails(
        self,
        folder: str = "INBOX",
        limit: int | None = None,
        since: str | None = None,
    ) -> AsyncIterator[Email]:
        """Fetch emails from a folder.

        For compatibility with EmailSource interface. Converts folder
        to a notmuch query.

        Args:
            folder: Folder name (converted to path query)
            limit: Maximum emails to fetch
            since: Only fetch emails since this date

        Yields:
            Email objects
        """
        # Build query
        query_parts = [f"folder:{folder}"]

        if since:
            # Convert IMAP date format to notmuch format
            query_parts.append(f"date:{since}..")

        query = " AND ".join(query_parts)
        async for email in self.fetch_by_query(query, limit=limit):
            yield email

    async def fetch_by_query(
        self, query: str, limit: int | None = None
    ) -> AsyncIterator[Email]:
        """Fetch emails matching a notmuch query.

        This is the primary method for NotmuchSource.

        Args:
            query: Notmuch query string
            limit: Maximum emails to fetch

        Yields:
            Email objects
        """
        # Get full message data with JSON output
        args = [
            "show",
            "--format=json",
            "--include-html",
            "--body=true",
            "--entire-thread=false",
        ]

        if limit:
            args.extend(["--limit", str(limit)])

        args.append(query)

        result = self._run_notmuch(args, check=False)

        if result.returncode != 0:
            if "No messages" in result.stderr or not result.stdout.strip():
                return
            raise NotmuchError(f"notmuch show failed: {result.stderr}")

        if not result.stdout.strip():
            return

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise NotmuchError(f"Failed to parse notmuch output: {e}")

        # notmuch show returns nested structure: [[[[message]]]]
        for thread in data:
            for message_group in thread:
                for message_data in message_group:
                    if isinstance(message_data, dict):
                        email = self._parse_message(message_data)
                        if email:
                            yield email

    async def fetch_unprocessed(
        self,
        hours: int | None = None,
        days: int | None = None,
        limit: int | None = None,
        additional_query: str | None = None,
    ) -> AsyncIterator[Email]:
        """Fetch unprocessed emails for the emma service.

        Args:
            hours: Only look at emails from the last N hours
            days: Only look at emails from the last N days (ignored if hours set)
            limit: Maximum emails to fetch
            additional_query: Extra query terms to add

        Yields:
            Email objects that haven't been processed
        """
        # Default to 24 hours if nothing specified
        if hours is None and days is None:
            hours = 24

        query_parts = [
            _date_query(days=days, hours=hours),
            f"NOT tag:{self.processed_tag}",
        ]

        if additional_query:
            query_parts.append(additional_query)

        query = " AND ".join(query_parts)
        async for email in self.fetch_by_query(query, limit=limit):
            yield email

    def _parse_message(self, data: dict[str, Any]) -> Email | None:
        """Parse notmuch JSON message data into Email object."""
        try:
            headers = data.get("headers", {})
            body_parts = data.get("body", [])

            # Extract body content
            body_text = ""
            body_html = None
            attachments: list[Attachment] = []

            for part in body_parts:
                content_type = part.get("content-type", "")
                content = part.get("content")

                if "attachment" in part.get("content-disposition", ""):
                    attachments.append(
                        Attachment(
                            filename=part.get("filename", "unnamed"),
                            content_type=content_type,
                            size=part.get("content-length", 0),
                            content_id=part.get("content-id"),
                        )
                    )
                elif content_type == "text/plain" and content:
                    body_text = content
                elif content_type == "text/html" and content:
                    body_html = content
                elif content_type.startswith("multipart/"):
                    # Recurse into multipart
                    self._extract_body_parts(
                        part.get("content", []),
                        body_text_out=[body_text] if body_text else [],
                        body_html_out=[body_html] if body_html else [],
                        attachments_out=attachments,
                    )

            # If only HTML, convert to text
            if not body_text and body_html:
                body_text = html_to_text(body_html)

            # Parse date
            date = None
            timestamp = data.get("timestamp")
            if timestamp:
                date = datetime.fromtimestamp(timestamp)
            elif headers.get("Date"):
                try:
                    date = parsedate_to_datetime(headers["Date"])
                except Exception:
                    pass

            # Parse addresses
            to_addrs = self._parse_address_list(headers.get("To", ""))
            cc_addrs = self._parse_address_list(headers.get("Cc", ""))

            # Extract folder from filename
            folder = "INBOX"
            filenames = data.get("filename", [])
            if filenames:
                filename = filenames[0] if isinstance(filenames, list) else filenames
                parts = filename.split("/")
                for i, part in enumerate(parts):
                    if part in ("cur", "new", "tmp") and i > 0:
                        folder = parts[i - 1]
                        break

            # Tags become flags
            tags = data.get("tags", [])
            flags = []
            if "unread" not in tags:
                flags.append("\\Seen")
            if "replied" in tags:
                flags.append("\\Answered")
            if "flagged" in tags:
                flags.append("\\Flagged")

            return Email(
                id=data.get("id", ""),
                source=self.name,
                message_id=data.get("id"),
                subject=headers.get("Subject", ""),
                from_addr=headers.get("From", ""),
                to_addrs=to_addrs,
                cc_addrs=cc_addrs,
                date=date,
                body_text=body_text,
                body_html=body_html,
                headers=headers,
                folder=folder,
                flags=flags,
                attachments=attachments,
                tags=tags,
            )
        except Exception:
            return None

    def _extract_body_parts(
        self,
        parts: list[dict[str, Any]],
        body_text_out: list[str],
        body_html_out: list[str | None],
        attachments_out: list[Attachment],
    ) -> None:
        """Recursively extract body parts from multipart message."""
        for part in parts:
            if not isinstance(part, dict):
                continue

            content_type = part.get("content-type", "")
            content = part.get("content")

            if "attachment" in part.get("content-disposition", ""):
                attachments_out.append(
                    Attachment(
                        filename=part.get("filename", "unnamed"),
                        content_type=content_type,
                        size=part.get("content-length", 0),
                        content_id=part.get("content-id"),
                    )
                )
            elif content_type == "text/plain" and content and not body_text_out:
                body_text_out.append(content)
            elif content_type == "text/html" and content and not body_html_out:
                body_html_out.append(content)
            elif content_type.startswith("multipart/") and isinstance(content, list):
                self._extract_body_parts(
                    content, body_text_out, body_html_out, attachments_out
                )

    def _parse_address_list(self, addr_string: str) -> list[str]:
        """Parse comma-separated address list."""
        if not addr_string:
            return []
        return [addr.strip() for addr in addr_string.split(",") if addr.strip()]

    async def get_email(self, email_id: str, folder: str = "INBOX") -> Email | None:
        """Fetch a specific email by message ID."""
        query = f"id:{email_id}"
        async for email in self.fetch_by_query(query, limit=1):
            return email
        return None

    async def move_email(self, email_id: str, from_folder: str, to_folder: str) -> bool:
        """Move email to another folder.

        Note: Notmuch doesn't directly support moving. This would require
        moving the underlying file and running 'notmuch new'. For now,
        we use tags to simulate folder-like organization.
        """
        # Add destination tag, remove source tag
        try:
            self._run_notmuch([
                "tag",
                f"+{to_folder.lower()}",
                f"-{from_folder.lower()}",
                f"id:{email_id}",
            ])
            return True
        except NotmuchError:
            return False

    async def delete_email(
        self, email_id: str, folder: str = "INBOX", *, permanent: bool = False
    ) -> bool:
        """Delete email by tagging.

        Note: Notmuch doesn't delete files. This adds a 'deleted' tag.
        Actual file deletion would require external handling.
        """
        try:
            if permanent:
                self._run_notmuch(["tag", "+deleted", f"id:{email_id}"])
            else:
                self._run_notmuch(["tag", "+trash", "-inbox", f"id:{email_id}"])
            return True
        except NotmuchError:
            return False

    async def set_flags(
        self, email_id: str, flags: list[str], folder: str = "INBOX"
    ) -> bool:
        """Set flags on an email using notmuch tags."""
        try:
            tag_args = ["tag"]

            for flag in flags:
                # Convert IMAP flags to notmuch tags
                if flag == "\\Seen":
                    tag_args.append("-unread")
                elif flag == "\\Answered":
                    tag_args.append("+replied")
                elif flag == "\\Flagged":
                    tag_args.append("+flagged")

            tag_args.append(f"id:{email_id}")
            self._run_notmuch(tag_args)
            return True
        except NotmuchError:
            return False

    async def add_tag(self, email_id: str, tag: str) -> bool:
        """Add a tag to an email."""
        try:
            self._run_notmuch(["tag", f"+{tag}", f"id:{email_id}"])
            return True
        except NotmuchError:
            return False

    async def remove_tag(self, email_id: str, tag: str) -> bool:
        """Remove a tag from an email."""
        try:
            self._run_notmuch(["tag", f"-{tag}", f"id:{email_id}"])
            return True
        except NotmuchError:
            return False

    async def mark_processed(self, email_id: str) -> bool:
        """Mark an email as processed by emma."""
        return await self.add_tag(email_id, self.processed_tag)

    async def is_processed(self, email_id: str) -> bool:
        """Check if an email has been processed."""
        count = await self.count(f"id:{email_id} AND tag:{self.processed_tag}")
        return count > 0
