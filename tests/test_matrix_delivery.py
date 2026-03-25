"""Tests for Matrix delivery plugin."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import RoomSendError, RoomSendResponse

from email_agent.config import DigestDeliveryConfig, Settings
from email_agent.models import DigestStatus
from email_agent.service.digest import DigestGenerator
from email_agent.service.plugins.base import PluginRegistry
from email_agent.service.plugins.delivery import FileDeliveryPlugin, MatrixDeliveryPlugin
from email_agent.service.state import ServiceState


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def state(temp_dir: Path) -> ServiceState:
    return ServiceState(temp_dir / "test.db")


@pytest.fixture
def settings(temp_dir: Path) -> Settings:
    return Settings(
        config_dir=temp_dir / "config",
        data_dir=temp_dir / "data",
        db_path=temp_dir / "test.db",
    )


@pytest.fixture
def matrix_plugin() -> MatrixDeliveryPlugin:
    return MatrixDeliveryPlugin()


@pytest.fixture
def matrix_config() -> dict[str, Any]:
    return {
        "type": "matrix",
        "homeserver": "https://matrix.example.com",
        "room_id": "!testroom:example.com",
        "access_token": "syt_test_token",
        "matrix_format": "html",
    }


@pytest.fixture
def registry() -> PluginRegistry:
    reg = PluginRegistry()
    reg.register_delivery(FileDeliveryPlugin())
    reg.register_delivery(MatrixDeliveryPlugin())
    return reg


class TestMatrixDeliveryPlugin:
    def test_delivery_type(self, matrix_plugin: MatrixDeliveryPlugin) -> None:
        assert matrix_plugin.delivery_type == "matrix"

    def test_description(self, matrix_plugin: MatrixDeliveryPlugin) -> None:
        assert matrix_plugin.description != ""

    @pytest.mark.asyncio
    async def test_deliver_missing_config(
        self, matrix_plugin: MatrixDeliveryPlugin, state: ServiceState
    ) -> None:
        digest = state.create_digest(
            period_start=datetime.now() - timedelta(hours=12),
            period_end=datetime.now(),
            email_count=1,
            summary="Test",
            raw_content="# Test Digest\n\nSome content.",
        )

        # Missing homeserver
        result = await matrix_plugin.deliver(digest, {"room_id": "!r:x", "access_token": "t"})
        assert result is False

        # Missing room_id
        result = await matrix_plugin.deliver(digest, {"homeserver": "https://x", "access_token": "t"})
        assert result is False

        # Missing access_token
        result = await matrix_plugin.deliver(digest, {"homeserver": "https://x", "room_id": "!r:x"})
        assert result is False

    @pytest.mark.asyncio
    async def test_deliver_no_content(
        self, matrix_plugin: MatrixDeliveryPlugin, state: ServiceState, matrix_config: dict
    ) -> None:
        digest = state.create_digest(
            period_start=datetime.now() - timedelta(hours=12),
            period_end=datetime.now(),
            email_count=0,
            summary="Empty",
            raw_content=None,
        )
        result = await matrix_plugin.deliver(digest, matrix_config)
        assert result is False

    @pytest.mark.asyncio
    async def test_deliver_success_html(
        self, matrix_plugin: MatrixDeliveryPlugin, state: ServiceState, matrix_config: dict
    ) -> None:
        digest = state.create_digest(
            period_start=datetime.now() - timedelta(hours=12),
            period_end=datetime.now(),
            email_count=2,
            summary="Test digest",
            raw_content="# Email Digest\n\n## Summary\n\nTest digest\n\n**Total Emails:** 2",
        )

        mock_response = RoomSendResponse.from_dict(
            {"event_id": "$testevent:example.com"},
            room_id="!testroom:example.com",
        )

        with patch("email_agent.service.plugins.delivery.matrix.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            MockClient.return_value = mock_client

            result = await matrix_plugin.deliver(digest, matrix_config)

            assert result is True
            mock_client.room_send.assert_called_once()
            call_kwargs = mock_client.room_send.call_args
            content = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
            assert content["msgtype"] == "m.text"
            assert content["format"] == "org.matrix.custom.html"
            assert "formatted_body" in content
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_success_markdown(
        self, matrix_plugin: MatrixDeliveryPlugin, state: ServiceState, matrix_config: dict
    ) -> None:
        matrix_config["matrix_format"] = "markdown"
        digest = state.create_digest(
            period_start=datetime.now() - timedelta(hours=12),
            period_end=datetime.now(),
            email_count=1,
            summary="Test",
            raw_content="# Digest\n\nContent here.",
        )

        mock_response = RoomSendResponse.from_dict(
            {"event_id": "$testevent:example.com"},
            room_id="!testroom:example.com",
        )

        with patch("email_agent.service.plugins.delivery.matrix.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            MockClient.return_value = mock_client

            result = await matrix_plugin.deliver(digest, matrix_config)

            assert result is True
            call_kwargs = mock_client.room_send.call_args
            content = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
            assert content["msgtype"] == "m.text"
            assert "format" not in content  # No HTML format for markdown mode

    @pytest.mark.asyncio
    async def test_deliver_send_failure(
        self, matrix_plugin: MatrixDeliveryPlugin, state: ServiceState, matrix_config: dict
    ) -> None:
        digest = state.create_digest(
            period_start=datetime.now() - timedelta(hours=12),
            period_end=datetime.now(),
            email_count=1,
            summary="Test",
            raw_content="# Digest\n\nContent.",
        )

        mock_error = MagicMock(spec=RoomSendError)
        mock_error.message = "Forbidden"

        with patch("email_agent.service.plugins.delivery.matrix.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(return_value=mock_error)
            mock_client.close = AsyncMock()
            MockClient.return_value = mock_client

            result = await matrix_plugin.deliver(digest, matrix_config)

            assert result is False
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_connection_error(
        self, matrix_plugin: MatrixDeliveryPlugin, state: ServiceState, matrix_config: dict
    ) -> None:
        digest = state.create_digest(
            period_start=datetime.now() - timedelta(hours=12),
            period_end=datetime.now(),
            email_count=1,
            summary="Test",
            raw_content="# Digest\n\nContent.",
        )

        with patch("email_agent.service.plugins.delivery.matrix.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.close = AsyncMock()
            MockClient.return_value = mock_client

            result = await matrix_plugin.deliver(digest, matrix_config)

            assert result is False
            mock_client.close.assert_called_once()


class TestMatrixFormatConversion:
    def test_markdown_to_html(self) -> None:
        plugin = MatrixDeliveryPlugin()
        md = "# Title\n\n## Section\n\n**bold** and *italic*\n\n- item 1\n- item 2"
        html = plugin._markdown_to_html(md)

        assert "<h1>" in html
        assert "<h2>" in html
        assert "<strong>" in html
        assert "<em>" in html
        assert "<li>" in html

    def test_markdown_to_text(self) -> None:
        plugin = MatrixDeliveryPlugin()
        md = "# Title\n\n**bold** and *italic*\n\n- item 1"
        text = plugin._markdown_to_text(md)

        assert "#" not in text
        assert "**" not in text
        assert "*" not in text or "italic" in text
        assert "•" in text

    def test_truncate_short(self) -> None:
        assert MatrixDeliveryPlugin._truncate("short text") == "short text"

    def test_truncate_long(self) -> None:
        long_text = "x" * 100_000
        result = MatrixDeliveryPlugin._truncate(long_text)
        assert len(result.encode("utf-8")) < 65_000
        assert "[... digest truncated due to size ...]" in result


class TestPluginRegistryIntegration:
    def test_registry_has_matrix(self, registry: PluginRegistry) -> None:
        plugin = registry.get_delivery_plugin("matrix")
        assert plugin is not None
        assert plugin.delivery_type == "matrix"

    def test_registry_has_file(self, registry: PluginRegistry) -> None:
        plugin = registry.get_delivery_plugin("file")
        assert plugin is not None

    def test_registry_lists_both(self, registry: PluginRegistry) -> None:
        types = registry.list_delivery_plugins()
        assert "file" in types
        assert "matrix" in types

    @pytest.mark.asyncio
    async def test_deliver_via_registry(
        self, settings: Settings, state: ServiceState, registry: PluginRegistry
    ) -> None:
        """Test that DigestGenerator uses the plugin registry for delivery."""
        settings.service.digest.delivery = [
            DigestDeliveryConfig(
                type="matrix",
                homeserver="https://matrix.example.com",
                room_id="!test:example.com",
                access_token="token",
            )
        ]

        state.mark_email_processed("e1", "imap", "INBOX")
        generator = DigestGenerator(settings, state, plugin_registry=registry)
        digest = await generator.generate(period_hours=12, force=True)
        assert digest is not None

        mock_response = RoomSendResponse.from_dict(
            {"event_id": "$evt:example.com"}, room_id="!test:example.com",
        )

        with patch("email_agent.service.plugins.delivery.matrix.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(return_value=mock_response)
            mock_client.close = AsyncMock()
            MockClient.return_value = mock_client

            success = await generator.deliver(digest)

            assert success is True
            mock_client.room_send.assert_called_once()

        updated = state.get_digest(digest.id)
        assert updated.delivery_status == DigestStatus.DELIVERED
