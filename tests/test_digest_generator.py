"""Tests for digest generation."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from email_agent.config import DigestConfig, DigestDeliveryConfig, Settings
from email_agent.models import DigestStatus
from email_agent.service.digest import DigestGenerator
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


class TestDigestGenerator:
    def test_init(self, settings: Settings, state: ServiceState) -> None:
        generator = DigestGenerator(settings, state)
        assert generator.config == settings.service.digest

    @pytest.mark.asyncio
    async def test_generate_no_emails(self, settings: Settings, state: ServiceState) -> None:
        generator = DigestGenerator(settings, state)

        digest = await generator.generate(period_hours=12)
        assert digest is None

    @pytest.mark.asyncio
    async def test_generate_with_force(self, settings: Settings, state: ServiceState) -> None:
        generator = DigestGenerator(settings, state)

        # Even with no emails, force should generate
        digest = await generator.generate(period_hours=12, force=True)
        assert digest is not None
        assert digest.email_count == 0

    @pytest.mark.asyncio
    async def test_generate_with_emails(self, settings: Settings, state: ServiceState) -> None:
        # Add some processed emails
        state.mark_email_processed(
            email_id="e1",
            source="imap",
            folder="INBOX",
            classification={"category": "work", "priority": "high"},
        )
        state.mark_email_processed(
            email_id="e2",
            source="imap",
            folder="INBOX",
            classification={"category": "personal", "priority": "normal"},
        )

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12)

        assert digest is not None
        assert digest.email_count == 2
        assert digest.summary is not None
        assert digest.raw_content is not None

    @pytest.mark.asyncio
    async def test_generate_below_threshold(self, settings: Settings, state: ServiceState) -> None:
        # Add one email
        state.mark_email_processed("e1", "imap", "INBOX")

        # Set threshold to 2
        settings.service.digest.min_emails = 2

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12)

        assert digest is None  # Below threshold

    @pytest.mark.asyncio
    async def test_generate_links_emails(self, settings: Settings, state: ServiceState) -> None:
        state.mark_email_processed("e1", "imap", "INBOX")
        state.mark_email_processed("e2", "imap", "INBOX")

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12)

        # Check that emails are now linked to the digest
        undigested = state.get_undigested_emails(
            since=datetime.now() - timedelta(hours=1)
        )
        assert len(undigested) == 0


class TestDigestDelivery:
    @pytest.mark.asyncio
    async def test_deliver_to_file(
        self, settings: Settings, state: ServiceState, temp_dir: Path
    ) -> None:
        state.mark_email_processed("e1", "imap", "INBOX")

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12, force=True)

        assert digest is not None

        # Deliver
        success = await generator.deliver(digest)
        assert success

        # Check file was created
        digests_dir = settings.data_dir / "digests"
        assert digests_dir.exists()
        files = list(digests_dir.glob("*.md"))
        assert len(files) == 1

        # Check digest status updated
        updated = state.get_digest(digest.id)
        assert updated.delivery_status == DigestStatus.DELIVERED

    @pytest.mark.asyncio
    async def test_deliver_html_format(
        self, settings: Settings, state: ServiceState, temp_dir: Path
    ) -> None:
        settings.service.digest.delivery = [
            DigestDeliveryConfig(type="file", format="html")
        ]

        state.mark_email_processed("e1", "imap", "INBOX")

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12, force=True)

        success = await generator.deliver(digest)
        assert success

        files = list((settings.data_dir / "digests").glob("*.html"))
        assert len(files) == 1

        # Verify HTML content
        content = files[0].read_text()
        assert "<html" in content

    @pytest.mark.asyncio
    async def test_deliver_text_format(
        self, settings: Settings, state: ServiceState, temp_dir: Path
    ) -> None:
        settings.service.digest.delivery = [
            DigestDeliveryConfig(type="file", format="text")
        ]

        state.mark_email_processed("e1", "imap", "INBOX")

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12, force=True)

        success = await generator.deliver(digest)
        assert success

        files = list((settings.data_dir / "digests").glob("*.txt"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_deliver_no_content(
        self, settings: Settings, state: ServiceState
    ) -> None:
        # Create a digest manually without content
        digest = state.create_digest(
            period_start=datetime.now() - timedelta(hours=12),
            period_end=datetime.now(),
            email_count=0,
            summary="Empty",
            raw_content=None,
        )

        generator = DigestGenerator(settings, state)
        success = await generator.deliver(digest)

        assert not success  # No content to deliver


class TestMarkdownGeneration:
    @pytest.mark.asyncio
    async def test_markdown_structure(
        self, settings: Settings, state: ServiceState
    ) -> None:
        state.mark_email_processed(
            "e1", "imap", "INBOX",
            classification={"category": "work", "priority": "high"},
        )
        state.mark_email_processed(
            "e2", "imap", "INBOX",
            classification={"category": "personal", "priority": "normal"},
        )

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12)

        assert digest is not None
        content = digest.raw_content

        # Check structure
        assert "# Email Digest" in content
        assert "## Summary" in content
        assert "## Emails by Category" in content
        assert "Work" in content or "work" in content.lower()
        assert "Personal" in content or "personal" in content.lower()

    @pytest.mark.asyncio
    async def test_markdown_includes_action_items(
        self, settings: Settings, state: ServiceState
    ) -> None:
        settings.service.digest.include_action_items = True

        state.mark_email_processed("e1", "imap", "INBOX")
        state.create_action_item(
            email_id="e1_hash",
            title="Important task",
        )

        generator = DigestGenerator(settings, state)
        digest = await generator.generate(period_hours=12)

        assert digest is not None
        content = digest.raw_content

        assert "## Action Items" in content
        assert "Important task" in content
