"""Maildir email source connector for local email storage."""

import email
import email.policy
import hashlib
import os
from collections.abc import AsyncIterator
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from email_agent.config import MaildirConfig
from email_agent.models import Attachment, Email

from .base import EmailSource


class MaildirSource(EmailSource):
    """Maildir email source for local Thunderbird/Maildir storage."""

    def __init__(
        self, config: MaildirConfig, name: str | None = None, trash_folder: str = "Trash"
    ) -> None:
        self.config = config
        self.name = name or config.account_name
        self.trash_folder = trash_folder
        self._connected = False

    async def connect(self) -> None:
        """Verify maildir exists."""
        if not self.config.path.exists():
            raise FileNotFoundError(f"Maildir path does not exist: {self.config.path}")
        self._connected = True

    async def disconnect(self) -> None:
        """No-op for local maildir."""
        self._connected = False

    async def list_folders(self) -> list[str]:
        """List available folders in the Maildir."""
        folders = ["INBOX"]
        base = self.config.path

        # Standard Maildir++ convention: .FolderName
        for item in base.iterdir():
            if item.is_dir() and item.name.startswith("."):
                folder_name = item.name[1:]  # Remove leading dot
                if folder_name and not folder_name.startswith("."):
                    folders.append(folder_name)

        # Also check for nested structure (Thunderbird style)
        for item in base.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                if (item / "cur").exists() or (item / "new").exists():
                    folders.append(item.name)

        return sorted(set(folders))

    def _get_folder_path(self, folder: str) -> Path:
        """Get the filesystem path for a folder."""
        base = self.config.path

        # Try direct subfolder first (Thunderbird/mbsync style)
        direct = base / folder
        if direct.exists() and (direct / "cur").exists():
            return direct

        # For INBOX, fall back to base if no INBOX subfolder
        if folder == "INBOX":
            if (base / "cur").exists():
                return base

        # Try Maildir++ convention (.FolderName)
        maildir_plus = base / f".{folder}"
        if maildir_plus.exists():
            return maildir_plus

        return direct  # Default to direct subfolder style

    def _generate_email_id(self, path: Path) -> str:
        """Generate a unique ID for an email based on its path."""
        return hashlib.sha256(str(path).encode()).hexdigest()[:16]

    async def fetch_emails(
        self,
        folder: str = "INBOX",
        limit: int | None = None,
        since: str | None = None,
    ) -> AsyncIterator[Email]:
        """Fetch emails from a Maildir folder."""
        folder_path = self._get_folder_path(folder)

        # Maildir has cur/ (read) and new/ (unread) subdirectories
        email_files: list[tuple[Path, bool]] = []

        cur_path = folder_path / "cur"
        new_path = folder_path / "new"

        if cur_path.exists():
            for f in cur_path.iterdir():
                if f.is_file():
                    email_files.append((f, True))

        if new_path.exists():
            for f in new_path.iterdir():
                if f.is_file():
                    email_files.append((f, False))

        # Sort by modification time, newest first
        email_files.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)

        if limit:
            email_files = email_files[:limit]

        for path, is_read in email_files:
            email_obj = await self._parse_maildir_file(path, folder, is_read)
            if email_obj:
                # Filter by date if since is specified
                if since and email_obj.date:
                    since_date = datetime.strptime(since, "%d-%b-%Y")
                    if email_obj.date.replace(tzinfo=None) < since_date:
                        continue
                yield email_obj

    async def _parse_maildir_file(
        self, path: Path, folder: str, is_read: bool
    ) -> Email | None:
        """Parse a maildir file into an Email object."""
        try:
            with open(path, "rb") as f:
                raw_message = f.read()

            msg: EmailMessage = email.message_from_bytes(
                raw_message, policy=email.policy.default
            )  # type: ignore

            # Extract body
            body_text = ""
            body_html = None
            attachments: list[Attachment] = []

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    disposition = str(part.get("Content-Disposition", ""))

                    if "attachment" in disposition:
                        payload = part.get_payload(decode=True)
                        attachments.append(
                            Attachment(
                                filename=part.get_filename() or "unnamed",
                                content_type=content_type,
                                size=len(payload) if payload else 0,
                                content_id=part.get("Content-ID"),
                            )
                        )
                    elif content_type == "text/plain" and not body_text:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body_text = payload.decode("utf-8", errors="replace")
                    elif content_type == "text/html" and not body_html:
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
            to_addrs = [
                addr.strip() for addr in (msg.get("To") or "").split(",") if addr.strip()
            ]
            cc_addrs = [
                addr.strip() for addr in (msg.get("Cc") or "").split(",") if addr.strip()
            ]

            # Parse flags from filename (Maildir convention)
            flags = []
            filename = path.name
            if ":2," in filename:
                flag_part = filename.split(":2,")[1]
                flag_map = {"S": "\\Seen", "R": "\\Answered", "F": "\\Flagged", "D": "\\Draft"}
                for char, flag in flag_map.items():
                    if char in flag_part:
                        flags.append(flag)

            if not is_read and "\\Seen" not in flags:
                pass  # New mail, not seen

            return Email(
                id=self._generate_email_id(path),
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
        except Exception:
            return None

    async def get_email(self, email_id: str, folder: str = "INBOX") -> Email | None:
        """Fetch a specific email by ID."""
        # Since IDs are hashes, we need to search
        async for email_obj in self.fetch_emails(folder):
            if email_obj.id == email_id:
                return email_obj
        return None

    async def move_email(self, email_id: str, from_folder: str, to_folder: str) -> bool:
        """Move email to another folder."""
        # Find the email file
        from_path = self._get_folder_path(from_folder)
        to_path = self._get_folder_path(to_folder)

        for subdir in ["cur", "new"]:
            src_dir = from_path / subdir
            if not src_dir.exists():
                continue

            for f in src_dir.iterdir():
                if self._generate_email_id(f) == email_id:
                    # Ensure destination exists
                    dest_dir = to_path / "cur"
                    dest_dir.mkdir(parents=True, exist_ok=True)

                    # Move file
                    dest = dest_dir / f.name
                    f.rename(dest)
                    return True

        return False

    async def delete_email(
        self, email_id: str, folder: str = "INBOX", *, permanent: bool = False
    ) -> bool:
        """Delete email from maildir.

        By default, moves the email to the trash folder (soft delete).
        Use permanent=True to permanently delete the email file.

        Args:
            email_id: The ID of the email to delete
            folder: The folder containing the email
            permanent: If True, permanently delete. If False, move to trash.

        Returns:
            True if deletion was successful
        """
        if permanent:
            # Permanent delete: remove the file
            folder_path = self._get_folder_path(folder)

            for subdir in ["cur", "new"]:
                src_dir = folder_path / subdir
                if not src_dir.exists():
                    continue

                for f in src_dir.iterdir():
                    if self._generate_email_id(f) == email_id:
                        f.unlink()
                        return True

            return False
        else:
            # Soft delete: move to trash folder
            if folder == self.trash_folder:
                # Already in trash, do permanent delete
                return await self.delete_email(email_id, folder, permanent=True)
            else:
                # Move to trash
                return await self.move_email(email_id, folder, self.trash_folder)

    async def set_flags(self, email_id: str, flags: list[str], folder: str = "INBOX") -> bool:
        """Set flags on an email by renaming the file."""
        folder_path = self._get_folder_path(folder)

        flag_map = {"\\Seen": "S", "\\Answered": "R", "\\Flagged": "F", "\\Draft": "D"}

        for subdir in ["cur", "new"]:
            src_dir = folder_path / subdir
            if not src_dir.exists():
                continue

            for f in src_dir.iterdir():
                if self._generate_email_id(f) == email_id:
                    # Build new filename with flags
                    base_name = f.name.split(":2,")[0] if ":2," in f.name else f.name
                    flag_str = "".join(flag_map.get(flag, "") for flag in sorted(flags))
                    new_name = f"{base_name}:2,{flag_str}"

                    # Move to cur/ with new name
                    dest_dir = folder_path / "cur"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    f.rename(dest_dir / new_name)
                    return True

        return False
