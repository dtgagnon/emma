"""Plugin system for Emma service extensibility."""

from .base import (
    DigestDeliveryPlugin,
    LLMCapabilityPlugin,
    PluginRegistry,
    RuleActionPlugin,
)
from .delivery import FileDeliveryPlugin

__all__ = [
    "DigestDeliveryPlugin",
    "FileDeliveryPlugin",
    "LLMCapabilityPlugin",
    "PluginRegistry",
    "RuleActionPlugin",
]

# Register built-in plugins
_default_registry = PluginRegistry()
_default_registry.register_delivery(FileDeliveryPlugin())


def get_default_registry() -> PluginRegistry:
    """Get the default plugin registry with built-in plugins."""
    return _default_registry
