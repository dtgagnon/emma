"""Tests for email monitoring."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from email_agent.config import MonitorConfig, Settings
from email_agent.models import Email, EmailCategory, EmailPriority
from email_agent.service.monitor import EmailMonitor
from email_agent.service.state import ServiceState


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def state(temp_dir: Path) -> ServiceState:
    """Create a temporary ServiceState for testing."""
    return ServiceState(temp_dir / "test.db")


@pytest.fixture
def settings(temp_dir: Path) -> Settings:
    """Create test settings."""
    return Settings(
        config_dir=temp_dir / "config",
        data_dir=temp_dir / "data",
        db_path=temp_dir / "test.db",
    )


@pytest.fixture
def sample_email() -> Email:
    return Email(
        id="test123",
        source="test_source",
        message_id="<test@example.com>",
        subject="Test Email",
        from_addr="sender@example.com",
        to_addrs=["recipient@example.com"],
        body_text="This is a test email body.",
        folder="INBOX",
        date=datetime.now(),
    )


class TestEmailMonitor:
    def test_init(self, settings: Settings, state: ServiceState) -> None:
        config = MonitorConfig()
        monitor = EmailMonitor(settings, state, config)

        assert monitor.settings == settings
        assert monitor.state == state
        assert monitor.config == config


class TestProcessEmail:
    @pytest.mark.asyncio
    async def test_process_email_basic(
        self, settings: Settings, state: ServiceState, sample_email: Email
    ) -> None:
        config = MonitorConfig(
            auto_classify=False,
            apply_rules=False,
            extract_actions=False,
        )
        monitor = EmailMonitor(settings, state, config)

        result = await monitor.process_email(sample_email)

        assert result["email_id"] == "test123"
        assert result["source"] == "test_source"
        assert result["errors"] == []

        # Should be marked as processed
        assert state.is_email_processed(
            sample_email.id,
            sample_email.source,
            sample_email.folder,
            sample_email.message_id,
        )

    @pytest.mark.asyncio
    async def test_process_email_with_classification(
        self, settings: Settings, state: ServiceState, sample_email: Email
    ) -> None:
        # Mock LLM processor
        mock_llm = MagicMock()
        mock_llm.classify_email = AsyncMock(
            return_value=(EmailCategory.WORK, EmailPriority.HIGH)
        )

        config = MonitorConfig(
            auto_classify=True,
            apply_rules=False,
            extract_actions=False,
        )
        monitor = EmailMonitor(settings, state, config, llm_processor=mock_llm)

        result = await monitor.process_email(sample_email)

        assert result["classification"] == {
            "category": "work",
            "priority": "high",
        }
        assert sample_email.category == EmailCategory.WORK
        assert sample_email.priority == EmailPriority.HIGH

    @pytest.mark.asyncio
    async def test_process_email_classification_error(
        self, settings: Settings, state: ServiceState, sample_email: Email
    ) -> None:
        # Mock LLM that raises error
        mock_llm = MagicMock()
        mock_llm.classify_email = AsyncMock(side_effect=Exception("LLM error"))

        config = MonitorConfig(
            auto_classify=True,
            apply_rules=False,
            extract_actions=False,
        )
        monitor = EmailMonitor(settings, state, config, llm_processor=mock_llm)

        result = await monitor.process_email(sample_email)

        assert len(result["errors"]) > 0
        assert "Classification error" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_process_email_with_action_extraction(
        self, settings: Settings, state: ServiceState, sample_email: Email
    ) -> None:
        # Mock action manager
        mock_action_manager = MagicMock()
        mock_item = MagicMock()
        mock_item.id = "action123"
        mock_action_manager.extract_from_email = AsyncMock(return_value=[mock_item])

        config = MonitorConfig(
            auto_classify=False,
            apply_rules=False,
            extract_actions=True,
        )
        monitor = EmailMonitor(
            settings, state, config, action_manager=mock_action_manager
        )

        result = await monitor.process_email(sample_email)

        assert result["action_items"] == ["action123"]


class TestRunCycle:
    @pytest.mark.asyncio
    async def test_run_cycle_no_emails(
        self, settings: Settings, state: ServiceState
    ) -> None:
        config = MonitorConfig()
        monitor = EmailMonitor(settings, state, config)

        # Mock poll_sources to return empty list
        monitor.poll_sources = AsyncMock(return_value=[])

        stats = await monitor.run_cycle()

        assert stats["emails_found"] == 0
        assert stats["emails_processed"] == 0
        assert stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_run_cycle_with_emails(
        self, settings: Settings, state: ServiceState, sample_email: Email
    ) -> None:
        config = MonitorConfig(
            auto_classify=False,
            apply_rules=False,
            extract_actions=False,
        )
        monitor = EmailMonitor(settings, state, config)

        # Mock poll_sources to return one email
        monitor.poll_sources = AsyncMock(return_value=[sample_email])

        stats = await monitor.run_cycle()

        assert stats["emails_found"] == 1
        assert stats["emails_processed"] == 1
        assert stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_run_cycle_handles_processing_error(
        self, settings: Settings, state: ServiceState, sample_email: Email
    ) -> None:
        config = MonitorConfig()
        monitor = EmailMonitor(settings, state, config)

        monitor.poll_sources = AsyncMock(return_value=[sample_email])
        monitor.process_email = AsyncMock(side_effect=Exception("Processing failed"))

        stats = await monitor.run_cycle()

        assert stats["emails_found"] == 1
        assert stats["errors"] >= 1


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_already_processed_email_skipped(
        self, settings: Settings, state: ServiceState, sample_email: Email
    ) -> None:
        # Mark email as already processed
        state.mark_email_processed(
            email_id=sample_email.id,
            source=sample_email.source,
            folder=sample_email.folder,
            message_id=sample_email.message_id,
        )

        config = MonitorConfig()
        monitor = EmailMonitor(settings, state, config)

        # Verify it's detected as processed
        assert state.is_email_processed(
            sample_email.id,
            sample_email.source,
            sample_email.folder,
            sample_email.message_id,
        )
