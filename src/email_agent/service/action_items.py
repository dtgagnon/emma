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

    async def extract_from_email(self, email: Email) -> list[ActionItem]:
        """Extract action items from an email using LLM.

        Args:
            email: The email to extract action items from.

        Returns:
            List of created ActionItem records.
        """
        if not self.llm_processor:
            logger.warning("No LLM processor configured, cannot extract action items")
            return []

        # Generate email hash for reference
        email_hash = _generate_email_hash(
            email.id, email.source, email.folder, email.message_id
        )

        try:
            # Extract detailed action items
            extracted = await self._extract_detailed(email)

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

    async def _extract_detailed(self, email: Email) -> list[dict[str, Any]]:
        """Extract detailed action items using LLM.

        Args:
            email: The email to analyze.

        Returns:
            List of action item dicts with title, description, priority, urgency, due_date, relevance.
        """
        # Build user identity context if available
        user_context = ""
        if self.llm_processor._user_email_lookup:
            user_email = self.llm_processor._get_user_email(email)
            if user_email:
                user_context = f"\nYou (the recipient): {user_email}"

        to_field = ", ".join(email.to_addrs) if email.to_addrs else "(unknown)"

        prompt = f"""Extract action items from this email that are relevant to the recipient.

From: {email.from_addr}
To: {to_field}
Subject: {email.subject}
Date: {email.date}{user_context}
Body:
{email.body_text[:3000]}

For each action item found, return a JSON object with:
- title: concise action item title (required)
- description: fuller description if needed
- priority: low, normal, high, or urgent
- urgency: low, normal, high, or urgent (how time-sensitive)
- due_date: ISO date if mentioned/implied (YYYY-MM-DD), null if not
- confidence: 0.0-1.0 how confident this is a real action item
- relevance: "direct" if someone is personally asking the recipient to do something, "informational" if it is a general announcement, newsletter CTA, or FYI

Guidelines for relevance:
- "direct": the sender explicitly asks the recipient to take a specific action (reply, review, schedule, submit, etc.)
- "informational": generic calls to action (click here, shop now, learn more), announcements that don't require the recipient to act, or actions mentioned in passing that aren't directed at the recipient

Return a JSON array of action items. Return [] if no action items found.

Example response:
[{{"title": "Reply to client", "priority": "high", "urgency": "high", "due_date": null, "confidence": 0.9, "relevance": "direct"}}]

Return ONLY valid JSON, no other text."""

        response = self.llm_processor._chat(prompt, max_tokens=500, temperature=0.1)

        try:
            result = self.llm_processor._parse_json(response)
            if isinstance(result, list):
                return result
        except (ValueError, Exception) as e:
            logger.warning(f"Failed to parse action items: {e}")

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
