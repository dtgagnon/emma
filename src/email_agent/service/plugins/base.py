"""Base plugin classes for Emma service extensibility."""

from abc import ABC, abstractmethod
from typing import Any

from ...models import Digest, Email


class LLMCapabilityPlugin(ABC):
    """Base class for LLM capability plugins.

    LLM capability plugins add new analysis or processing capabilities
    that leverage the LLM processor.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this capability."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of this capability."""
        return ""

    @abstractmethod
    async def analyze(self, email: Email, llm_processor: Any) -> dict[str, Any]:
        """Analyze an email using this capability.

        Args:
            email: The email to analyze.
            llm_processor: The LLM processor instance.

        Returns:
            Dict containing the analysis results.
        """
        ...


class RuleActionPlugin(ABC):
    """Base class for rule action plugins.

    Rule action plugins add new action types that can be triggered
    by automation rules.
    """

    @property
    @abstractmethod
    def action_type(self) -> str:
        """The action type identifier (used in rule definitions)."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of this action."""
        return ""

    @abstractmethod
    async def execute(
        self,
        email: Email,
        params: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> bool:
        """Execute the action on an email.

        Args:
            email: The email to act upon.
            params: Action parameters from the rule definition.
            dry_run: If True, only simulate the action.

        Returns:
            True if the action succeeded (or would succeed in dry-run mode).
        """
        ...


class DigestDeliveryPlugin(ABC):
    """Base class for digest delivery plugins.

    Digest delivery plugins handle delivering generated digests
    through various channels (file, email, webhook, etc.).
    """

    @property
    @abstractmethod
    def delivery_type(self) -> str:
        """The delivery type identifier (used in config)."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of this delivery method."""
        return ""

    @abstractmethod
    async def deliver(
        self,
        digest: Digest,
        config: dict[str, Any],
    ) -> bool:
        """Deliver a digest.

        Args:
            digest: The digest to deliver.
            config: Delivery configuration.

        Returns:
            True if delivery succeeded.
        """
        ...


class PluginRegistry:
    """Registry for managing Emma service plugins."""

    def __init__(self) -> None:
        """Initialize the plugin registry."""
        self._llm_capabilities: dict[str, LLMCapabilityPlugin] = {}
        self._rule_actions: dict[str, RuleActionPlugin] = {}
        self._delivery_plugins: dict[str, DigestDeliveryPlugin] = {}

    def register_llm_capability(self, plugin: LLMCapabilityPlugin) -> None:
        """Register an LLM capability plugin.

        Args:
            plugin: The plugin to register.
        """
        self._llm_capabilities[plugin.name] = plugin

    def register_rule_action(self, plugin: RuleActionPlugin) -> None:
        """Register a rule action plugin.

        Args:
            plugin: The plugin to register.
        """
        self._rule_actions[plugin.action_type] = plugin

    def register_delivery(self, plugin: DigestDeliveryPlugin) -> None:
        """Register a digest delivery plugin.

        Args:
            plugin: The plugin to register.
        """
        self._delivery_plugins[plugin.delivery_type] = plugin

    def get_llm_capability(self, name: str) -> LLMCapabilityPlugin | None:
        """Get an LLM capability plugin by name."""
        return self._llm_capabilities.get(name)

    def get_rule_action(self, action_type: str) -> RuleActionPlugin | None:
        """Get a rule action plugin by type."""
        return self._rule_actions.get(action_type)

    def get_delivery_plugin(self, delivery_type: str) -> DigestDeliveryPlugin | None:
        """Get a delivery plugin by type."""
        return self._delivery_plugins.get(delivery_type)

    def list_llm_capabilities(self) -> list[str]:
        """List all registered LLM capability names."""
        return list(self._llm_capabilities.keys())

    def list_rule_actions(self) -> list[str]:
        """List all registered rule action types."""
        return list(self._rule_actions.keys())

    def list_delivery_plugins(self) -> list[str]:
        """List all registered delivery types."""
        return list(self._delivery_plugins.keys())
