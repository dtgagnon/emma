"""Action item extraction and management."""

import logging
from datetime import datetime
from typing import Any

from ..config import ActionItemConfig
from ..models import ActionItem, ActionItemStatus, Email, EmailPriority
from ..processors.llm import LLMProcessor
from .state import ServiceState, _generate_email_hash

logger = logging.getLogger(__name__)


class ActionItemManager:
    """Manages action items extracted from emails."""

    def __init__(
        self,
        state: ServiceState,
        llm_processor: LLMProcessor | None = None,
        config: ActionItemConfig | None = None,
    ) -> None:
        """Initialize the action item manager.

        Args:
            state: Service state manager.
            llm_processor: Optional LLM processor for extraction.
            config: Optional action item configuration.
        """
        self.state = state
        self.llm_processor = llm_processor
        self.config = config or ActionItemConfig()

    async def extract_from_email(
        self, email: Email, analysis: dict[str, Any] | None = None
    ) -> list[ActionItem]:
        """Extract action items from an email, using pre-computed analysis if available.

        Args:
            email: The email to extract action items from.
            analysis: Pre-computed analysis dict from LLMProcessor.analyze_email().
                      If provided, action_items are read directly without an LLM call.

        Returns:
            List of created ActionItem records.
        """
        # Generate email hash for reference
        email_hash = _generate_email_hash(
            email.id, email.source, email.folder, email.message_id
        )

        try:
            # Get action items from pre-computed analysis or run analysis
            if analysis is not None:
                extracted = analysis.get("action_items", [])
            elif self.llm_processor:
                result = await self.llm_processor.analyze_email(email)
                extracted = result.get("action_items", [])
            else:
                logger.warning("No LLM processor configured, cannot extract action items")
                return []

            # Filter by confidence threshold
            pre_filter_count = len(extracted)
            extracted = [
                i for i in extracted
                if i.get("confidence", 1.0) >= self.config.confidence_threshold
            ]
            filtered_count = pre_filter_count - len(extracted)
            if filtered_count > 0:
                logger.debug(
                    f"Filtered {filtered_count} action items below confidence "
                    f"threshold {self.config.confidence_threshold}"
                )

            items: list[ActionItem] = []
            for item_data in extracted:
                # Parse priority
                priority_str = item_data.get("priority", "normal").lower()
                try:
                    priority = EmailPriority(priority_str)
                except ValueError:
                    priority = EmailPriority.NORMAL

                # Parse due date if present
                due_date = None
                if item_data.get("due_date"):
                    try:
                        due_date = datetime.fromisoformat(item_data["due_date"])
                    except (ValueError, TypeError):
                        pass

                # Create action item
                item = self.state.create_action_item(
                    email_id=email_hash,
                    title=item_data.get("title", "Untitled action"),
                    description=item_data.get("description"),
                    priority=priority,
                    urgency=item_data.get("urgency", "normal"),
                    due_date=due_date,
                    relevance=item_data.get("relevance", "direct"),
                    metadata={
                        "email_subject": email.subject,
                        "email_from": email.from_addr,
                        "confidence": item_data.get("confidence", 1.0),
                    },
                )
                items.append(item)

            return items

        except Exception as e:
            logger.error(f"Error extracting action items from {email.id}: {e}")
            return []

    async def create(
        self,
        email_id: str,
        title: str,
        *,
        description: str | None = None,
        priority: EmailPriority = EmailPriority.NORMAL,
        urgency: str = "normal",
        due_date: datetime | None = None,
        metadata: dict | None = None,
    ) -> ActionItem:
        """Create a new action item manually.

        Args:
            email_id: The processed email hash ID.
            title: The action item title.
            description: Optional description.
            priority: Priority level.
            urgency: Urgency level.
            due_date: Optional due date.
            metadata: Optional additional metadata.

        Returns:
            The created ActionItem.
        """
        return self.state.create_action_item(
            email_id=email_id,
            title=title,
            description=description,
            priority=priority,
            urgency=urgency,
            due_date=due_date,
            metadata=metadata,
        )

    def list(
        self,
        *,
        status: ActionItemStatus | None = None,
        priority: EmailPriority | None = None,
        relevance: str | None = None,
        limit: int = 50,
    ) -> list[ActionItem]:
        """List action items with optional filters.

        Args:
            status: Filter by status.
            priority: Filter by priority.
            relevance: Filter by relevance ("direct" or "informational"). None for all.
            limit: Maximum items to return.

        Returns:
            List of matching ActionItem records.
        """
        return self.state.list_action_items(
            status=status,
            priority=priority,
            relevance=relevance,
            limit=limit,
        )

    def get(self, item_id: str) -> ActionItem | None:
        """Get a specific action item.

        Args:
            item_id: The action item UUID.

        Returns:
            The ActionItem if found, None otherwise.
        """
        return self.state.get_action_item(item_id)

    def complete(self, item_id: str) -> bool:
        """Mark an action item as completed.

        Args:
            item_id: The action item UUID.

        Returns:
            True if updated, False if not found.
        """
        return self.state.update_action_status(item_id, ActionItemStatus.COMPLETED)

    def dismiss(self, item_id: str) -> bool:
        """Dismiss an action item.

        Args:
            item_id: The action item UUID.

        Returns:
            True if updated, False if not found.
        """
        return self.state.update_action_status(item_id, ActionItemStatus.DISMISSED)

    def start(self, item_id: str) -> bool:
        """Mark an action item as in progress.

        Args:
            item_id: The action item UUID.

        Returns:
            True if updated, False if not found.
        """
        return self.state.update_action_status(item_id, ActionItemStatus.IN_PROGRESS)
