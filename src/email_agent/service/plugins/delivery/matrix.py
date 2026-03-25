"""Matrix delivery plugin for Emma digests."""

import logging
import re
from pathlib import Path
from typing import Any

from nio import AsyncClient, RoomSendResponse

from ....models import Digest
from ..base import DigestDeliveryPlugin

logger = logging.getLogger(__name__)

# Matrix has a ~65KB event size limit; leave room for envelope overhead
MAX_MESSAGE_BYTES = 60_000


class MatrixDeliveryPlugin(DigestDeliveryPlugin):
    """Delivers digests to a Matrix room as formatted messages."""

    @property
    def delivery_type(self) -> str:
        return "matrix"

    @property
    def description(self) -> str:
        return "Send digest to a Matrix room"

    async def deliver(
        self,
        digest: Digest,
        config: dict[str, Any],
    ) -> bool:
        """Deliver a digest to a Matrix room.

        Config options:
            homeserver: Matrix homeserver URL (required).
            room_id: Target room ID (required).
            access_token: Authentication token (required).
            matrix_format: "html" or "markdown". Default: html.

        Args:
            digest: The digest to deliver.
            config: Delivery configuration.

        Returns:
            True if delivery succeeded.
        """
        if not digest.raw_content:
            logger.warning(f"Digest {digest.id} has no content to deliver")
            return False

        homeserver = config.get("homeserver")
        room_id = config.get("room_id")
        access_token = config.get("access_token")

        # Load from env file if provided (sops-nix pattern) — overrides inline values
        if config.get("matrix_env_file"):
            try:
                env = self._parse_env_file(Path(config["matrix_env_file"]))
                homeserver = env.get("HOMESERVER") or homeserver
                room_id = env.get("ROOM_ID") or room_id
                access_token = env.get("ACCESS_TOKEN") or access_token
            except Exception as e:
                logger.error(f"Matrix delivery: could not read matrix_env_file: {e}")
                return False

        if not all([homeserver, room_id, access_token]):
            missing = [k for k, v in {
                "homeserver": homeserver,
                "room_id": room_id,
                "access_token": access_token,
            }.items() if not v]
            logger.error(f"Matrix delivery missing required config: {', '.join(missing)}")
            return False

        matrix_format = config.get("matrix_format", "html")

        # Prepare message content
        plain_body = self._markdown_to_text(digest.raw_content)

        if matrix_format == "html":
            formatted_body = self._markdown_to_html(digest.raw_content)
            content = {
                "msgtype": "m.text",
                "body": self._truncate(plain_body),
                "format": "org.matrix.custom.html",
                "formatted_body": self._truncate(formatted_body),
            }
        else:
            # Send as plain markdown — many Matrix clients render it natively
            content = {
                "msgtype": "m.text",
                "body": self._truncate(digest.raw_content),
            }

        # Send to Matrix
        client = AsyncClient(homeserver)
        client.access_token = access_token
        # user_id isn't strictly required for sending but set a placeholder
        client.user_id = "@emma:bot"

        try:
            response = await client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
            )
            if isinstance(response, RoomSendResponse):
                logger.info(f"Delivered digest to Matrix room {room_id} (event: {response.event_id})")
                return True
            else:
                logger.error(f"Matrix delivery failed: {response}")
                return False
        except Exception as e:
            logger.error(f"Matrix delivery error: {e}")
            return False
        finally:
            await client.close()

    @staticmethod
    def _parse_env_file(path: Path) -> dict[str, str]:
        """Parse a KEY=value env file into a dict."""
        result = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def _truncate(text: str) -> str:
        """Truncate text to stay within Matrix event size limits."""
        encoded = text.encode("utf-8")
        if len(encoded) <= MAX_MESSAGE_BYTES:
            return text
        # Truncate at byte boundary, then decode safely
        truncated = encoded[:MAX_MESSAGE_BYTES].decode("utf-8", errors="ignore")
        return truncated + "\n\n[... digest truncated due to size ...]"

    @staticmethod
    def _markdown_to_html(markdown: str) -> str:
        """Convert markdown to Matrix-compatible HTML."""
        import html as html_module

        content = html_module.escape(markdown)

        # Headers
        content = re.sub(r"^### (.+)$", r"<h3>\1</h3>", content, flags=re.MULTILINE)
        content = re.sub(r"^## (.+)$", r"<h2>\1</h2>", content, flags=re.MULTILINE)
        content = re.sub(r"^# (.+)$", r"<h1>\1</h1>", content, flags=re.MULTILINE)

        # Bold and italic
        content = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", content)
        content = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", content)

        # Code
        content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)

        # Lists
        content = re.sub(r"^\s*- (.+)$", r"<li>\1</li>", content, flags=re.MULTILINE)

        # Horizontal rules
        content = re.sub(r"^---+$", r"<hr>", content, flags=re.MULTILINE)

        # Paragraphs (double newlines)
        content = re.sub(r"\n\n+", r"<br/><br/>", content)
        # Single newlines to <br/>
        content = re.sub(r"\n", r"<br/>", content)

        return content

    @staticmethod
    def _markdown_to_text(markdown: str) -> str:
        """Convert markdown to plain text for the fallback body."""
        text = markdown
        text = re.sub(r"#{1,6}\s*", "", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = re.sub(r"^\s*-\s*", "• ", text, flags=re.MULTILINE)
        text = re.sub(r"---+", "-" * 40, text)
        return text
