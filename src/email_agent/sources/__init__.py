"""Email source connectors."""

from .base import EmailSource
from .imap import IMAPSource
from .maildir import MaildirSource
from .notmuch import NotmuchError, NotmuchSource

__all__ = [
    "EmailSource",
    "IMAPSource",
    "MaildirSource",
    "NotmuchError",
    "NotmuchSource",
]
