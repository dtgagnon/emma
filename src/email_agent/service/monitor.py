"""Email monitoring for the Emma service."""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from ..config import MonitorConfig, Settings
from ..models import Email
from ..processors.llm import LLMProcessor
from ..processors.rules import RulesEngine
from ..sources.base import EmailSource
from ..sources.imap import IMAPSource
from ..sources.maildir import MaildirSource
from ..sources.notmuch import NotmuchError, NotmuchSource
from .state import ServiceState

if TYPE_CHECKING:
    from .action_items import ActionItemManager

logger = logging.getLogger(__name__)


class EmailMonitor:
    """Monitors email sources for new messages and processes them."""

    def __init__(
        self,
        settings: Settings,
        state: ServiceState,
        config: MonitorConfig,
        llm_processor: LLMProcessor | None = None,
        rules_engine: RulesEngine | None = None,
        action_manager: "ActionItemManager | None" = None,
    ) -> None:
        """Initialize the email monitor.

        Args:
            settings: Application settings.
            state: Service state manager.
            config: Monitor configuration.
            llm_processor: Optional LLM processor for classification/analysis.
            rules_engine: Optional rules engine for automation.
            action_manager: Optional action item manager for extraction.
        """
        self.settings = settings
        self.state = state
        self.config = config
        self.llm_processor = llm_processor
        self.rules_engine = rules_engine
        self.action_manager = action_manager

    def _get_notmuch_source(self) -> NotmuchSource | None:
        """Get NotmuchSource if enabled and available.

        Returns:
            NotmuchSource instance or None if not available.
        """
        if not self.settings.notmuch.enabled:
            return None

        try:
            source = NotmuchSource(
                name="notmuch",
                processed_tag=self.settings.notmuch.processed_tag,
                database_path=(
                    str(self.settings.notmuch.database_path)
                    if self.settings.notmuch.database_path
                    else None
                ),
            )
            return source
        except Exception as e:
            logger.warning(f"Failed to create NotmuchSource: {e}")
            return None

    def _get_sources(self) -> list[tuple[str, EmailSource]]:
        """Get the email sources to monitor.

        Returns:
            List of (name, source) tuples.
        """
        sources: list[tuple[str, EmailSource]] = []

        # Filter to configured sources, or use all if none specified
        filter_sources = set(self.config.sources) if self.config.sources else None

        # Add IMAP sources
        for name, imap_config in self.settings.imap_accounts.items():
            if filter_sources is None or name in filter_sources:
                sources.append((name, IMAPSource(imap_config, name)))

        # Add Maildir sources (fallback if notmuch not used)
        for name, maildir_config in self.settings.maildir_accounts.items():
            if filter_sources is None or name in filter_sources:
                sources.append((name, MaildirSource(maildir_config)))

        return sources

    async def poll_sources(self) -> list[Email]:
        """Poll all configured sources for new emails.

        Returns:
            List of new (unprocessed) emails.
        """
        new_emails: list[Email] = []

        # Try NotmuchSource first (preferred)
        notmuch_source = self._get_notmuch_source()
        if notmuch_source:
            try:
                await notmuch_source.connect()

                # Build exclusion query from config
                exclude_query = ""
                if self.settings.notmuch.exclude_tags:
                    exclude_parts = [
                        f"NOT tag:{tag}" for tag in self.settings.notmuch.exclude_tags
                    ]
                    exclude_query = " AND ".join(exclude_parts)

                logger.debug("Polling notmuch for unprocessed emails")
                async for email in notmuch_source.fetch_unprocessed(
                    hours=24,  # Look at last 24 hours
                    limit=self.settings.batch_size,
                    additional_query=exclude_query if exclude_query else None,
                ):
                    new_emails.append(email)

                await notmuch_source.disconnect()
                logger.info(f"Found {len(new_emails)} new emails via notmuch")
                return new_emails

            except NotmuchError as e:
                logger.warning(f"Notmuch polling failed, falling back to sources: {e}")
            except Exception as e:
                logger.error(f"Error polling notmuch: {e}")

        # Fallback to individual sources
        sources = self._get_sources()

        for source_name, source in sources:
            try:
                async with source:
                    for folder in self.config.folders:
                        try:
                            logger.debug(f"Polling {source_name}/{folder}")
                            async for email in source.fetch_emails(
                                folder=folder,
                                limit=self.settings.batch_size,
                            ):
                                # Check if already processed
                                if not self.state.is_email_processed(
                                    email_id=email.id,
                                    source=source_name,
                                    folder=folder,
                                    message_id=email.message_id,
                                ):
                                    new_emails.append(email)
                        except Exception as e:
                            logger.error(f"Error polling {source_name}/{folder}: {e}")
            except Exception as e:
                logger.error(f"Error connecting to {source_name}: {e}")

        logger.info(f"Found {len(new_emails)} new emails")
        return new_emails

    async def process_email(self, email: Email) -> dict:
        """Process a single email.

        Performs classification, rule processing, and action extraction
        based on configuration.

        Args:
            email: The email to process.

        Returns:
            Dict with processing results including classification, actions, etc.
        """
        result = {
            "email_id": email.id,
            "source": email.source,
            "folder": email.folder,
            "classification": None,
            "llm_analysis": None,
            "rules_applied": [],
            "action_items": [],
            "errors": [],
        }

        # Classify with LLM if enabled
        if self.config.auto_classify and self.llm_processor:
            try:
                category, priority = await self.llm_processor.classify_email(email)
                email.category = category
                email.priority = priority
                result["classification"] = {
                    "category": category.value,
                    "priority": priority.value,
                }
                logger.debug(f"Classified {email.id}: {category.value}/{priority.value}")
            except Exception as e:
                logger.error(f"Error classifying {email.id}: {e}")
                result["errors"].append(f"Classification error: {e}")

        # Apply rules if enabled
        if self.config.apply_rules and self.rules_engine:
            try:
                rule_result = await self.rules_engine.process_email(email)
                result["rules_applied"] = rule_result.rules_matched
                if rule_result.errors:
                    result["errors"].extend(rule_result.errors)
            except Exception as e:
                logger.error(f"Error applying rules to {email.id}: {e}")
                result["errors"].append(f"Rules error: {e}")

        # Extract action items if enabled
        if self.config.extract_actions and self.action_manager:
            try:
                items = await self.action_manager.extract_from_email(email)
                result["action_items"] = [item.id for item in items]
                logger.debug(f"Extracted {len(items)} action items from {email.id}")
            except Exception as e:
                logger.error(f"Error extracting actions from {email.id}: {e}")
                result["errors"].append(f"Action extraction error: {e}")

        # Mark as processed in state DB
        self.state.mark_email_processed(
            email_id=email.id,
            source=email.source,
            folder=email.folder,
            message_id=email.message_id,
            classification=result["classification"],
            llm_analysis=result["llm_analysis"],
        )

        # Also mark as processed in notmuch if using notmuch source
        if self.settings.notmuch.enabled and email.message_id:
            try:
                notmuch = self._get_notmuch_source()
                if notmuch:
                    await notmuch.connect()
                    await notmuch.mark_processed(email.message_id)
                    await notmuch.disconnect()
            except Exception as e:
                logger.warning(f"Failed to mark email as processed in notmuch: {e}")

        return result

    async def run_cycle(self) -> dict:
        """Run a complete monitoring cycle.

        Polls all sources, processes new emails, and returns statistics.

        Returns:
            Dict with cycle statistics.
        """
        cycle_start = datetime.now()
        stats = {
            "started_at": cycle_start.isoformat(),
            "emails_found": 0,
            "emails_processed": 0,
            "errors": 0,
            "action_items_created": 0,
        }

        try:
            # Poll for new emails
            new_emails = await self.poll_sources()
            stats["emails_found"] = len(new_emails)

            # Process each email
            for email in new_emails:
                try:
                    result = await self.process_email(email)
                    stats["emails_processed"] += 1
                    stats["action_items_created"] += len(result.get("action_items", []))
                    if result.get("errors"):
                        stats["errors"] += len(result["errors"])
                except Exception as e:
                    logger.error(f"Error processing email {email.id}: {e}")
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Error in monitoring cycle: {e}")
            stats["errors"] += 1

        stats["duration_seconds"] = (datetime.now() - cycle_start).total_seconds()
        logger.info(
            f"Monitoring cycle complete: {stats['emails_processed']} processed, "
            f"{stats['errors']} errors"
        )

        return stats
