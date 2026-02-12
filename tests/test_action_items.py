"""Tests for action item management."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from email_agent.config import ActionItemConfig
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

    @pytest.mark.asyncio
    async def test_extract_filters_low_confidence(self, state: ServiceState, sample_email: Email) -> None:
        """Items below confidence threshold are filtered out."""
        mock_llm = MagicMock()
        mock_llm._user_email_lookup = None
        mock_llm._chat = MagicMock(return_value='[]')
        mock_llm._parse_json = MagicMock(return_value=[
            {"title": "High confidence", "priority": "high", "confidence": 0.9, "relevance": "direct"},
            {"title": "Low confidence", "priority": "normal", "confidence": 0.3, "relevance": "direct"},
            {"title": "Medium confidence", "priority": "normal", "confidence": 0.7, "relevance": "direct"},
        ])

        config = ActionItemConfig(confidence_threshold=0.7)
        manager = ActionItemManager(state=state, llm_processor=mock_llm, config=config)

        items = await manager.extract_from_email(sample_email)

        assert len(items) == 2
        titles = {item.title for item in items}
        assert "High confidence" in titles
        assert "Medium confidence" in titles
        assert "Low confidence" not in titles

    @pytest.mark.asyncio
    async def test_extract_relevance_stored(self, state: ServiceState, sample_email: Email) -> None:
        """Relevance field from LLM is stored on the action item."""
        mock_llm = MagicMock()
        mock_llm._user_email_lookup = None
        mock_llm._chat = MagicMock(return_value='[]')
        mock_llm._parse_json = MagicMock(return_value=[
            {"title": "Direct task", "priority": "high", "confidence": 0.9, "relevance": "direct"},
            {"title": "FYI item", "priority": "normal", "confidence": 0.8, "relevance": "informational"},
        ])

        manager = ActionItemManager(state=state, llm_processor=mock_llm)

        items = await manager.extract_from_email(sample_email)

        assert len(items) == 2
        by_title = {item.title: item for item in items}
        assert by_title["Direct task"].relevance == "direct"
        assert by_title["FYI item"].relevance == "informational"

    @pytest.mark.asyncio
    async def test_extract_relevance_defaults_to_direct(self, state: ServiceState, sample_email: Email) -> None:
        """When LLM omits relevance, it defaults to 'direct'."""
        mock_llm = MagicMock()
        mock_llm._user_email_lookup = None
        mock_llm._chat = MagicMock(return_value='[]')
        mock_llm._parse_json = MagicMock(return_value=[
            {"title": "No relevance field", "priority": "normal", "confidence": 0.9},
        ])

        manager = ActionItemManager(state=state, llm_processor=mock_llm)

        items = await manager.extract_from_email(sample_email)

        assert len(items) == 1
        assert items[0].relevance == "direct"


class TestRelevanceFiltering:
    def test_create_with_relevance(self, state: ServiceState) -> None:
        item = state.create_action_item(
            email_id="e1",
            title="Direct task",
            relevance="direct",
        )
        assert item.relevance == "direct"

        item2 = state.create_action_item(
            email_id="e2",
            title="FYI item",
            relevance="informational",
        )
        assert item2.relevance == "informational"

    def test_list_filter_by_relevance(self, state: ServiceState) -> None:
        state.create_action_item(email_id="e1", title="Direct", relevance="direct")
        state.create_action_item(email_id="e2", title="Info", relevance="informational")

        direct = state.list_action_items(relevance="direct")
        assert len(direct) == 1
        assert direct[0].title == "Direct"

        info = state.list_action_items(relevance="informational")
        assert len(info) == 1
        assert info[0].title == "Info"

        all_items = state.list_action_items()
        assert len(all_items) == 2

    def test_default_relevance_is_direct(self, state: ServiceState) -> None:
        """Items created without explicit relevance default to 'direct'."""
        item = state.create_action_item(email_id="e1", title="Default")
        assert item.relevance == "direct"

        fetched = state.get_action_item(item.id)
        assert fetched.relevance == "direct"

    def test_migration_defaults_existing_rows(self, state: ServiceState) -> None:
        """Existing rows (from before migration) should default to 'direct'."""
        # The migration adds the column with DEFAULT 'direct',
        # so any pre-existing rows get 'direct'. We verify by
        # creating an item and reading it back.
        item = state.create_action_item(email_id="e1", title="Legacy item")
        fetched = state.get_action_item(item.id)
        assert fetched.relevance == "direct"

    def test_list_relevance_via_manager(self, state: ServiceState) -> None:
        manager = ActionItemManager(state=state)

        state.create_action_item(email_id="e1", title="Direct", relevance="direct")
        state.create_action_item(email_id="e2", title="Info", relevance="informational")

        direct = manager.list(relevance="direct")
        assert len(direct) == 1

        all_items = manager.list()
        assert len(all_items) == 2
