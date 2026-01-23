"""Emma background service for email monitoring and automation."""

from .action_items import ActionItemManager
from .daemon import EmmaService
from .digest import DigestGenerator
from .monitor import EmailMonitor
from .state import ServiceState

__all__ = [
    "ActionItemManager",
    "DigestGenerator",
    "EmailMonitor",
    "EmmaService",
    "ServiceState",
]
