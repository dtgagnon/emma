"""IMAP email source connector."""

import email
import email.policy
from collections.abc import AsyncIterator
from datetime import datetime
from email.message import EmailMessage
from typing import Any

from imapclient import IMAPClient

from email_agent.config import IMAPConfig
from email_agent.models import Attachment, Email

from .base import EmailSource


class IMAPSource(EmailSource):
    """IMAP email source connector."""

    def __init__(
        self, config: IMAPConfig, name: str = "imap", trash_folder: str = "Trash"
    ) -> None:
        self.config = config
        self.name = name
        self.trash_folder = trash_folder
        self._client: IMAPClient | None = None

    async def connect(self) -> None:
        """Connect to IMAP server."""
        self._client = IMAPClient(
            self.config.host,
            port=self.config.port,
            ssl=self.config.use_ssl,
        )
        self._client.login(self.config.username, self.config.password)

    async def disconnect(self) -> None:
        """Disconnect from IMAP server."""
        if self._client:
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    @property
    def client(self) -> IMAPClient:
        if self._client is None:
            raise RuntimeError("Not connected to IMAP server")
        return self._client

    async def list_folders(self) -> list[str]:
        """List available IMAP folders."""
        folders = self.client.list_folders()
        return [f[2] for f in folders]  # Return folder names

    async def fetch_emails(
        self,
        folder: str = "INBOX",
        limit: int | None = None,
        since: str | None = None,
    ) -> AsyncIterator[Email]:
        """Fetch emails from IMAP folder."""
        self.client.select_folder(folder)

        # Build search criteria
        criteria: list[Any] = ["ALL"]
        if since:
            criteria = ["SINCE", since]

        message_ids = self.client.search(criteria)

        if limit:
            message_ids = message_ids[-limit:]

        # Fetch in batches
        for msg_id in message_ids:
            email_obj = await self.get_email(str(msg_id), folder)
            if email_obj:
                yield email_obj

    async def get_email(self, email_id: str, folder: str = "INBOX") -> Email | None:
        """Fetch a specific email by UID."""
        self.client.select_folder(folder)

        uid = int(email_id)
        response = self.client.fetch([uid], ["RFC822", "FLAGS"])

        if uid not in response:
            return None

        data = response[uid]
        raw_message = data[b"RFC822"]
        flags = [f.decode() if isinstance(f, bytes) else str(f) for f in data.get(b"FLAGS", [])]

        # Parse the email
        msg: EmailMessage = email.message_from_bytes(raw_message, policy=email.policy.default)  # type: ignore

        # Extract body
        body_text = ""
        body_html = None
        attachments: list[Attachment] = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in disposition:
                    attachments.append(
                        Attachment(
                            filename=part.get_filename() or "unnamed",
                            content_type=content_type,
                            size=len(part.get_payload(decode=True) or b""),
                            content_id=part.get("Content-ID"),
                        )
                    )
                elif content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode("utf-8", errors="replace")
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_html = payload.decode("utf-8", errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode("utf-8", errors="replace")

        # Parse date
        date_str = msg.get("Date")
        date = None
        if date_str:
            try:
                from email.utils import parsedate_to_datetime

                date = parsedate_to_datetime(date_str)
            except Exception:
                pass

        # Parse addresses
        to_addrs = [addr.strip() for addr in (msg.get("To") or "").split(",") if addr.strip()]
        cc_addrs = [addr.strip() for addr in (msg.get("Cc") or "").split(",") if addr.strip()]

        return Email(
            id=email_id,
            source=self.name,
            message_id=msg.get("Message-ID"),
            subject=msg.get("Subject", ""),
            from_addr=msg.get("From", ""),
            to_addrs=to_addrs,
            cc_addrs=cc_addrs,
            date=date,
            body_text=body_text,
            body_html=body_html,
            headers=dict(msg.items()),
            folder=folder,
            flags=flags,
            attachments=attachments,
        )

    async def move_email(self, email_id: str, from_folder: str, to_folder: str) -> bool:
        """Move email to another folder."""
        try:
            self.client.select_folder(from_folder)
            self.client.move([int(email_id)], to_folder)
            return True
        except Exception:
            return False

    async def delete_email(
        self, email_id: str, folder: str = "INBOX", *, permanent: bool = False
    ) -> bool:
        """Delete email.

        By default, moves the email to the trash folder (soft delete).
        Use permanent=True to permanently delete the email.

        Args:
            email_id: The UID of the email to delete
            folder: The folder containing the email
            permanent: If True, permanently delete. If False, move to trash.

        Returns:
            True if deletion was successful
        """
        try:
            if permanent:
                # Permanent delete: mark as deleted and expunge
                self.client.select_folder(folder)
                self.client.delete_messages([int(email_id)])
                self.client.expunge()
            else:
                # Soft delete: move to trash folder
                if folder == self.trash_folder:
                    # Already in trash, do permanent delete
                    self.client.select_folder(folder)
                    self.client.delete_messages([int(email_id)])
                    self.client.expunge()
                else:
                    # Move to trash
                    return await self.move_email(email_id, folder, self.trash_folder)
            return True
        except Exception:
            return False

    async def set_flags(self, email_id: str, flags: list[str], folder: str = "INBOX") -> bool:
        """Set flags on an email."""
        try:
            self.client.select_folder(folder)
            self.client.set_flags([int(email_id)], flags)
            return True
        except Exception:
            return False
