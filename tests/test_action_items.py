"""Tests for action item management."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from email_agent.models import ActionItemStatus, Email, EmailPriority
from email_agent.service.action_items import ActionItemManager
from email_agent.service.state import ServiceState


@pytest.fixture
def state() -> ServiceState:
    """Create a temporary ServiceState for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield ServiceState(db_path)


@pytest.fixture
def sample_email() -> Email:
    return Email(
        id="test123",
        source="test",
        subject="Action Required: Review proposal by Friday",
        from_addr="boss@company.com",
        to_addrs=["me@company.com"],
        body_text="""Hi,

Please review the attached proposal by end of day Friday.
Also, schedule a meeting with the client for next week.
Don't forget to update the project timeline.

Thanks,
Boss""",
        folder="INBOX",
        date=datetime.now(),
    )


class TestActionItemManager:
    def test_create_action_item(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        item = manager.state.create_action_item(
            email_id="email_hash",
            title="Review proposal",
            priority=EmailPriority.HIGH,
        )

        assert item.title == "Review proposal"
        assert item.priority == EmailPriority.HIGH

    def test_list_action_items(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        state.create_action_item(email_id="e1", title="Task 1")
        state.create_action_item(email_id="e2", title="Task 2")

        items = manager.list()
        assert len(items) == 2

    def test_list_with_status_filter(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        state.create_action_item(email_id="e1", title="Pending")
        item = state.create_action_item(email_id="e2", title="Completed")
        state.update_action_status(item.id, ActionItemStatus.COMPLETED)

        pending = manager.list(status=ActionItemStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].title == "Pending"

    def test_list_with_priority_filter(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        state.create_action_item(email_id="e1", title="Normal", priority=EmailPriority.NORMAL)
        state.create_action_item(email_id="e2", title="Urgent", priority=EmailPriority.URGENT)

        urgent = manager.list(priority=EmailPriority.URGENT)
        assert len(urgent) == 1
        assert urgent[0].title == "Urgent"

    def test_get_action_item(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        created = state.create_action_item(email_id="e1", title="Test task")
        fetched = manager.get(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.title == "Test task"

    def test_complete_action_item(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        item = state.create_action_item(email_id="e1", title="Task")
        assert manager.complete(item.id)

        updated = manager.get(item.id)
        assert updated.status == ActionItemStatus.COMPLETED
        assert updated.completed_at is not None

    def test_dismiss_action_item(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        item = state.create_action_item(email_id="e1", title="Task")
        assert manager.dismiss(item.id)

        updated = manager.get(item.id)
        assert updated.status == ActionItemStatus.DISMISSED

    def test_start_action_item(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        item = state.create_action_item(email_id="e1", title="Task")
        assert manager.start(item.id)

        updated = manager.get(item.id)
        assert updated.status == ActionItemStatus.IN_PROGRESS


class TestActionItemExtraction:
    @pytest.mark.asyncio
    async def test_extract_without_llm(self, state: ServiceState, sample_email: Email) -> None:
        manager = ActionItemManager(state=state, llm_processor=None)

        items = await manager.extract_from_email(sample_email)
        assert items == []  # No LLM means no extraction

    @pytest.mark.asyncio
    async def test_extract_with_mocked_llm(self, state: ServiceState, sample_email: Email) -> None:
        # Mock LLM processor
        mock_llm = MagicMock()
        mock_llm._chat = MagicMock(return_value='[{"title": "Review proposal", "priority": "high", "urgency": "high", "due_date": null, "confidence": 0.9}]')
        mock_llm._parse_json = MagicMock(return_value=[
            {"title": "Review proposal", "priority": "high", "urgency": "high", "due_date": None, "confidence": 0.9}
        ])

        manager = ActionItemManager(state=state, llm_processor=mock_llm)

        items = await manager.extract_from_email(sample_email)

        assert len(items) == 1
        assert items[0].title == "Review proposal"
        assert items[0].priority == EmailPriority.HIGH

    @pytest.mark.asyncio
    async def test_extract_handles_llm_error(self, state: ServiceState, sample_email: Email) -> None:
        # Mock LLM that raises an error
        mock_llm = MagicMock()
        mock_llm._chat = MagicMock(side_effect=Exception("LLM error"))

        manager = ActionItemManager(state=state, llm_processor=mock_llm)

        items = await manager.extract_from_email(sample_email)
        assert items == []  # Should handle error gracefully
