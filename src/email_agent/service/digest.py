"""Digest generation for the Emma service."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import DigestConfig, DigestDeliveryConfig, Settings
from ..models import ActionItem, Digest, DigestStatus, ProcessedEmail
from ..processors.llm import LLMProcessor
from .state import ServiceState

logger = logging.getLogger(__name__)


class DigestGenerator:
    """Generates email digests from processed emails."""

    def __init__(
        self,
        settings: Settings,
        state: ServiceState,
        llm_processor: LLMProcessor | None = None,
    ) -> None:
        """Initialize the digest generator.

        Args:
            settings: Application settings.
            state: Service state manager.
            llm_processor: Optional LLM processor for summaries.
        """
        self.settings = settings
        self.state = state
        self.llm_processor = llm_processor
        self.config = settings.service.digest

    async def generate(
        self,
        period_hours: int | None = None,
        *,
        force: bool = False,
    ) -> Digest | None:
        """Generate a digest for the specified period.

        Args:
            period_hours: Hours to include in digest. Defaults to config value.
            force: Generate even if under minimum email threshold.

        Returns:
            The created Digest, or None if no emails to include.
        """
        period = period_hours or self.config.period_hours
        period_end = datetime.now()
        period_start = period_end - timedelta(hours=period)

        # Get undigested emails from the period
        emails = self.state.get_undigested_emails(since=period_start)

        if not emails and not force:
            logger.info("No undigested emails found")
            return None

        if len(emails) < self.config.min_emails and not force:
            logger.info(f"Only {len(emails)} emails, below threshold of {self.config.min_emails}")
            return None

        # Generate digest content
        summary = await self._generate_summary(emails)
        raw_content = await self._generate_markdown(emails, summary)

        # Create digest record
        digest = self.state.create_digest(
            period_start=period_start,
            period_end=period_end,
            email_count=len(emails),
            summary=summary,
            raw_content=raw_content,
        )

        # Update emails with digest_id
        for email in emails:
            self.state.update_email_digest_id(email.id, digest.id)

        logger.info(f"Generated digest {digest.id} with {len(emails)} emails")
        return digest

    async def _generate_summary(self, emails: list[ProcessedEmail]) -> str:
        """Generate an executive summary of the emails.

        Args:
            emails: List of processed emails.

        Returns:
            Executive summary string.
        """
        if not emails:
            return "No emails to summarize."

        if not self.llm_processor:
            return f"Digest contains {len(emails)} emails."

        # Build email summaries for LLM
        email_summaries = []
        for email in emails[:20]:  # Limit to avoid token overflow
            classification = email.classification or {}
            email_summaries.append({
                "source": email.source,
                "folder": email.folder,
                "category": classification.get("category", "unknown"),
                "priority": classification.get("priority", "normal"),
            })

        prompt = f"""Generate a brief executive summary of this email digest.

Emails in this digest:
{self._format_email_list(email_summaries)}

Total emails: {len(emails)}

Write a 2-3 sentence summary highlighting:
- Overall email volume and sources
- Any high-priority items
- Key categories of emails received

Summary:"""

        try:
            summary = self.llm_processor._chat(prompt, max_tokens=200, temperature=0.3)
            return summary.strip()
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return f"Digest contains {len(emails)} emails."

    def _format_email_list(self, emails: list[dict]) -> str:
        """Format email list for LLM prompt."""
        lines = []
        for i, email in enumerate(emails, 1):
            lines.append(
                f"{i}. {email['source']}/{email['folder']} - "
                f"{email['category']} ({email['priority']})"
            )
        return "\n".join(lines)

    async def _generate_markdown(
        self,
        emails: list[ProcessedEmail],
        summary: str,
    ) -> str:
        """Generate full markdown content for the digest.

        Args:
            emails: List of processed emails.
            summary: Executive summary.

        Returns:
            Full markdown content.
        """
        now = datetime.now()
        lines = [
            f"# Email Digest - {now.strftime('%Y-%m-%d %H:%M')}",
            "",
            "## Summary",
            "",
            summary,
            "",
            f"**Total Emails:** {len(emails)}",
            "",
        ]

        # Group by category
        by_category: dict[str, list[ProcessedEmail]] = {}
        for email in emails:
            category = (email.classification or {}).get("category", "other")
            by_category.setdefault(category, []).append(email)

        lines.append("## Emails by Category")
        lines.append("")

        for category, cat_emails in sorted(by_category.items()):
            lines.append(f"### {category.title()} ({len(cat_emails)})")
            lines.append("")
            for email in cat_emails:
                priority = (email.classification or {}).get("priority", "normal")
                priority_marker = "🔴" if priority == "urgent" else "🟡" if priority == "high" else ""
                lines.append(f"- {priority_marker} {email.source}/{email.folder}: `{email.email_id}`")
            lines.append("")

        # Add action items if enabled
        if self.config.include_action_items:
            action_items = self.state.list_action_items(limit=20)
            pending_items = [
                item for item in action_items
                if item.status.value == "pending"
            ]

            if pending_items:
                lines.append("## Action Items")
                lines.append("")
                for item in pending_items:
                    priority_marker = "🔴" if item.priority.value == "urgent" else "🟡" if item.priority.value == "high" else ""
                    due_str = f" (due: {item.due_date.strftime('%Y-%m-%d')})" if item.due_date else ""
                    lines.append(f"- {priority_marker} **{item.title}**{due_str}")
                    if item.description:
                        lines.append(f"  {item.description}")
                lines.append("")

        lines.append("---")
        lines.append(f"*Generated by Emma at {now.isoformat()}*")

        return "\n".join(lines)

    async def deliver(self, digest: Digest) -> bool:
        """Deliver a digest via configured delivery methods.

        Args:
            digest: The digest to deliver.

        Returns:
            True if at least one delivery succeeded.
        """
        if not digest.raw_content:
            logger.warning(f"Digest {digest.id} has no content to deliver")
            return False

        # Get delivery configs, default to file if none configured
        delivery_configs = self.config.delivery
        if not delivery_configs:
            delivery_configs = [DigestDeliveryConfig()]

        success = False
        for delivery_config in delivery_configs:
            try:
                if delivery_config.type == "file":
                    delivered = await self._deliver_file(digest, delivery_config)
                    if delivered:
                        success = True
                else:
                    logger.warning(f"Unknown delivery type: {delivery_config.type}")
            except Exception as e:
                logger.error(f"Delivery failed ({delivery_config.type}): {e}")

        # Update digest status
        status = DigestStatus.DELIVERED if success else DigestStatus.FAILED
        self.state.update_digest_status(digest.id, status)

        return success

    async def _deliver_file(
        self,
        digest: Digest,
        config: DigestDeliveryConfig,
    ) -> bool:
        """Deliver digest to a file.

        Args:
            digest: The digest to deliver.
            config: Delivery configuration.

        Returns:
            True if successful.
        """
        # Determine output directory
        output_dir = config.output_dir or (self.settings.data_dir / "digests")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        timestamp = digest.created_at.strftime("%Y%m%d_%H%M%S")
        extension = {
            "markdown": "md",
            "html": "html",
            "text": "txt",
        }.get(config.format, "md")
        filename = f"digest_{timestamp}.{extension}"
        filepath = output_dir / filename

        # Convert content if needed
        content = digest.raw_content or ""
        if config.format == "html":
            content = self._markdown_to_html(content)
        elif config.format == "text":
            content = self._markdown_to_text(content)

        # Write file
        filepath.write_text(content)
        logger.info(f"Delivered digest to {filepath}")

        return True

    def _markdown_to_html(self, markdown: str) -> str:
        """Convert markdown to simple HTML."""
        # Basic conversion - could use markdown library for better results
        html = markdown
        html = html.replace("# ", "<h1>").replace("\n## ", "</h1>\n<h2>")
        html = html.replace("\n### ", "</h2>\n<h3>")
        html = html.replace("**", "<strong>").replace("*", "<em>")
        html = html.replace("\n- ", "\n<li>")
        html = html.replace("`", "<code>")

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Email Digest</title>
    <style>
        body {{ font-family: sans-serif; max-width: 800px; margin: 2em auto; padding: 0 1em; }}
        h1, h2, h3 {{ color: #333; }}
        li {{ margin: 0.5em 0; }}
        code {{ background: #f4f4f4; padding: 0.2em 0.4em; }}
    </style>
</head>
<body>
{html}
</body>
</html>"""

    def _markdown_to_text(self, markdown: str) -> str:
        """Convert markdown to plain text."""
        import re
        text = markdown
        # Remove markdown formatting
        text = re.sub(r"#{1,6}\s*", "", text)  # Headers
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # Bold
        text = re.sub(r"\*([^*]+)\*", r"\1", text)  # Italic
        text = re.sub(r"`([^`]+)`", r"\1", text)  # Code
        text = re.sub(r"^\s*-\s*", "• ", text, flags=re.MULTILINE)  # Lists
        return text
