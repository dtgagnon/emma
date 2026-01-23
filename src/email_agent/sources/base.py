"""Base class for email source connectors."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from email_agent.models import Email


class EmailSource(ABC):
    """Abstract base class for email source connectors."""

    name: str
    trash_folder: str = "Trash"  # Default trash folder name

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the email source."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the email source."""
        ...

    @abstractmethod
    async def list_folders(self) -> list[str]:
        """List available folders/mailboxes."""
        ...

    @abstractmethod
    async def fetch_emails(
        self,
        folder: str = "INBOX",
        limit: int | None = None,
        since: str | None = None,
    ) -> AsyncIterator[Email]:
        """Fetch emails from a folder.

        Args:
            folder: Folder to fetch from
            limit: Maximum number of emails to fetch
            since: Only fetch emails since this date (IMAP date format)

        Yields:
            Email objects
        """
        ...

    @abstractmethod
    async def get_email(self, email_id: str, folder: str = "INBOX") -> Email | None:
        """Fetch a specific email by ID."""
        ...

    @abstractmethod
    async def move_email(self, email_id: str, from_folder: str, to_folder: str) -> bool:
        """Move an email to a different folder."""
        ...

    @abstractmethod
    async def delete_email(
        self, email_id: str, folder: str = "INBOX", *, permanent: bool = False
    ) -> bool:
        """Delete an email.

        By default, moves the email to the trash folder (soft delete).
        Use permanent=True to permanently delete the email.

        Args:
            email_id: The ID of the email to delete
            folder: The folder containing the email
            permanent: If True, permanently delete. If False, move to trash.

        Returns:
            True if deletion was successful
        """
        ...

    @abstractmethod
    async def set_flags(self, email_id: str, flags: list[str], folder: str = "INBOX") -> bool:
        """Set flags on an email (e.g., \\Seen, \\Flagged)."""
        ...

    async def __aenter__(self) -> "EmailSource":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()
