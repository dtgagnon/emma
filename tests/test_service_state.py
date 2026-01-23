"""Tests for service state management."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from email_agent.models import ActionItemStatus, DigestStatus, EmailPriority
from email_agent.service.state import ServiceState, _generate_email_hash


@pytest.fixture
def state() -> ServiceState:
    """Create a temporary ServiceState for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield ServiceState(db_path)


class TestEmailHashing:
    def test_hash_with_message_id(self) -> None:
        hash1 = _generate_email_hash("123", "source", "INBOX", message_id="<msg@test.com>")
        hash2 = _generate_email_hash("456", "other", "Sent", message_id="<msg@test.com>")
        # Same message_id should produce same hash
        assert hash1 == hash2

    def test_hash_without_message_id(self) -> None:
        hash1 = _generate_email_hash("123", "source", "INBOX")
        hash2 = _generate_email_hash("123", "source", "INBOX")
        hash3 = _generate_email_hash("123", "source", "Sent")
        assert hash1 == hash2
        assert hash1 != hash3


class TestProcessedEmails:
    def test_mark_email_processed(self, state: ServiceState) -> None:
        result = state.mark_email_processed(
            email_id="test123",
            source="imap",
            folder="INBOX",
            message_id="<test@example.com>",
            classification={"category": "work", "priority": "high"},
        )
        assert result.email_id == "test123"
        assert result.source == "imap"
        assert result.classification == {"category": "work", "priority": "high"}

    def test_is_email_processed(self, state: ServiceState) -> None:
        assert not state.is_email_processed("test123", "imap", "INBOX")

        state.mark_email_processed(
            email_id="test123",
            source="imap",
            folder="INBOX",
        )

        assert state.is_email_processed("test123", "imap", "INBOX")

    def test_is_email_processed_by_message_id(self, state: ServiceState) -> None:
        state.mark_email_processed(
            email_id="test123",
            source="imap",
            folder="INBOX",
            message_id="<unique@test.com>",
        )

        # Same message_id should be detected even with different email_id
        assert state.is_email_processed(
            "different456", "other_source", "Sent", message_id="<unique@test.com>"
        )

    def test_get_processed_emails(self, state: ServiceState) -> None:
        state.mark_email_processed("email1", "imap", "INBOX")
        state.mark_email_processed("email2", "imap", "INBOX")
        state.mark_email_processed("email3", "maildir", "INBOX")

        all_emails = state.get_processed_emails()
        assert len(all_emails) == 3

        imap_emails = state.get_processed_emails(source="imap")
        assert len(imap_emails) == 2

    def test_get_undigested_emails(self, state: ServiceState) -> None:
        state.mark_email_processed("email1", "imap", "INBOX")
        state.mark_email_processed("email2", "imap", "INBOX", digest_id="digest123")

        since = datetime.now() - timedelta(hours=1)
        undigested = state.get_undigested_emails(since)

        assert len(undigested) == 1
        assert undigested[0].email_id == "email1"


class TestDigests:
    def test_create_digest(self, state: ServiceState) -> None:
        now = datetime.now()
        digest = state.create_digest(
            period_start=now - timedelta(hours=12),
            period_end=now,
            email_count=10,
            summary="Test summary",
            raw_content="# Test Digest\n\nContent here",
        )

        assert digest.email_count == 10
        assert digest.summary == "Test summary"
        assert digest.delivery_status == DigestStatus.PENDING

    def test_get_digest(self, state: ServiceState) -> None:
        now = datetime.now()
        created = state.create_digest(
            period_start=now - timedelta(hours=12),
            period_end=now,
            email_count=5,
            summary="Summary",
        )

        fetched = state.get_digest(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.email_count == 5

    def test_list_digests(self, state: ServiceState) -> None:
        now = datetime.now()
        for i in range(5):
            state.create_digest(
                period_start=now - timedelta(hours=12),
                period_end=now,
                email_count=i,
                summary=f"Digest {i}",
            )

        digests = state.list_digests(limit=3)
        assert len(digests) == 3

    def test_update_digest_status(self, state: ServiceState) -> None:
        now = datetime.now()
        digest = state.create_digest(
            period_start=now - timedelta(hours=12),
            period_end=now,
            email_count=1,
            summary="Test",
        )

        assert state.update_digest_status(digest.id, DigestStatus.DELIVERED)

        updated = state.get_digest(digest.id)
        assert updated.delivery_status == DigestStatus.DELIVERED


class TestActionItems:
    def test_create_action_item(self, state: ServiceState) -> None:
        item = state.create_action_item(
            email_id="email_hash_123",
            title="Reply to client",
            description="Need to respond about the proposal",
            priority=EmailPriority.HIGH,
            urgency="urgent",
            due_date=datetime.now() + timedelta(days=1),
        )

        assert item.title == "Reply to client"
        assert item.priority == EmailPriority.HIGH
        assert item.status == ActionItemStatus.PENDING

    def test_get_action_item(self, state: ServiceState) -> None:
        created = state.create_action_item(
            email_id="email_hash",
            title="Test action",
        )

        fetched = state.get_action_item(created.id)
        assert fetched is not None
        assert fetched.title == "Test action"

    def test_list_action_items_by_status(self, state: ServiceState) -> None:
        state.create_action_item(email_id="e1", title="Pending 1")
        state.create_action_item(email_id="e2", title="Pending 2")

        item = state.create_action_item(email_id="e3", title="Will complete")
        state.update_action_status(item.id, ActionItemStatus.COMPLETED)

        pending = state.list_action_items(status=ActionItemStatus.PENDING)
        assert len(pending) == 2

        completed = state.list_action_items(status=ActionItemStatus.COMPLETED)
        assert len(completed) == 1

    def test_list_action_items_by_priority(self, state: ServiceState) -> None:
        state.create_action_item(
            email_id="e1", title="Normal", priority=EmailPriority.NORMAL
        )
        state.create_action_item(
            email_id="e2", title="High", priority=EmailPriority.HIGH
        )
        state.create_action_item(
            email_id="e3", title="Urgent", priority=EmailPriority.URGENT
        )

        high = state.list_action_items(priority=EmailPriority.HIGH)
        assert len(high) == 1
        assert high[0].title == "High"

    def test_update_action_status(self, state: ServiceState) -> None:
        item = state.create_action_item(email_id="e1", title="Test")

        assert state.update_action_status(item.id, ActionItemStatus.IN_PROGRESS)
        updated = state.get_action_item(item.id)
        assert updated.status == ActionItemStatus.IN_PROGRESS

        assert state.update_action_status(item.id, ActionItemStatus.COMPLETED)
        completed = state.get_action_item(item.id)
        assert completed.status == ActionItemStatus.COMPLETED
        assert completed.completed_at is not None


class TestCleanup:
    def test_cleanup_old_data(self, state: ServiceState) -> None:
        # This is harder to test since we'd need to manipulate timestamps
        # For now just verify the method runs without error
        deleted = state.cleanup_old_data(days=30)
        assert "processed_emails" in deleted
        assert "digests" in deleted
        assert "action_items" in deleted


class TestStats:
    def test_get_stats(self, state: ServiceState) -> None:
        state.mark_email_processed("e1", "imap", "INBOX")
        state.mark_email_processed("e2", "imap", "INBOX")
        state.create_action_item(email_id="e1_hash", title="Action 1")
        state.create_action_item(email_id="e2_hash", title="Action 2")

        stats = state.get_stats()

        assert stats["total_processed_emails"] == 2
        assert stats["total_action_items"] == 2
        assert stats["emails_last_24h"] == 2
        assert "pending" in stats["action_items_by_status"]
