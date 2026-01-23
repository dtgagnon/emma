"""Email source connectors."""

from .base import EmailSource
from .imap import IMAPSource
from .maildir import MaildirSource

__all__ = ["EmailSource", "IMAPSource", "MaildirSource"]
