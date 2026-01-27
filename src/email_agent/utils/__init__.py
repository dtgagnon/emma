"""Utility modules for email processing."""

from email_agent.utils.text import (
    prepare_body,
    smart_truncate,
    strip_mobile_footers,
    strip_quoted_replies,
)

__all__ = [
    "prepare_body",
    "smart_truncate",
    "strip_mobile_footers",
    "strip_quoted_replies",
]
