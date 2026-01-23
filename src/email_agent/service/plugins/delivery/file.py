"""File delivery plugin for Emma digests."""

import logging
import re
from pathlib import Path
from typing import Any

from ....models import Digest
from ..base import DigestDeliveryPlugin

logger = logging.getLogger(__name__)


class FileDeliveryPlugin(DigestDeliveryPlugin):
    """Delivers digests to local files."""

    @property
    def delivery_type(self) -> str:
        return "file"

    @property
    def description(self) -> str:
        return "Save digest to local file (markdown, HTML, or text)"

    async def deliver(
        self,
        digest: Digest,
        config: dict[str, Any],
    ) -> bool:
        """Deliver a digest to a file.

        Config options:
            output_dir: Directory to write files to.
            format: Output format (markdown, html, text). Default: markdown.
            filename_template: Template for filename. Default: digest_{timestamp}.{ext}

        Args:
            digest: The digest to deliver.
            config: Delivery configuration.

        Returns:
            True if delivery succeeded.
        """
        if not digest.raw_content:
            logger.warning(f"Digest {digest.id} has no content to deliver")
            return False

        # Get output directory
        output_dir_str = config.get("output_dir")
        if output_dir_str:
            output_dir = Path(output_dir_str).expanduser()
        else:
            output_dir = Path.home() / ".local" / "share" / "emma" / "digests"

        output_dir.mkdir(parents=True, exist_ok=True)

        # Determine format and extension
        format_type = config.get("format", "markdown")
        extension = {
            "markdown": "md",
            "html": "html",
            "text": "txt",
        }.get(format_type, "md")

        # Generate filename
        timestamp = digest.created_at.strftime("%Y%m%d_%H%M%S")
        template = config.get("filename_template", "digest_{timestamp}.{ext}")
        filename = template.format(timestamp=timestamp, ext=extension, id=digest.id)
        filepath = output_dir / filename

        # Convert content
        content = digest.raw_content
        if format_type == "html":
            content = self._markdown_to_html(content)
        elif format_type == "text":
            content = self._markdown_to_text(content)

        # Write file
        try:
            filepath.write_text(content)
            logger.info(f"Delivered digest to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to write digest to {filepath}: {e}")
            return False

    def _markdown_to_html(self, markdown: str) -> str:
        """Convert markdown to HTML."""
        import html as html_module

        # Escape HTML entities first
        content = html_module.escape(markdown)

        # Convert headers
        content = re.sub(r"^### (.+)$", r"<h3>\1</h3>", content, flags=re.MULTILINE)
        content = re.sub(r"^## (.+)$", r"<h2>\1</h2>", content, flags=re.MULTILINE)
        content = re.sub(r"^# (.+)$", r"<h1>\1</h1>", content, flags=re.MULTILINE)

        # Convert bold and italic
        content = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", content)
        content = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", content)

        # Convert code
        content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)

        # Convert lists (basic)
        content = re.sub(r"^\s*- (.+)$", r"<li>\1</li>", content, flags=re.MULTILINE)

        # Convert horizontal rules
        content = re.sub(r"^---+$", r"<hr>", content, flags=re.MULTILINE)

        # Convert paragraphs (double newlines)
        content = re.sub(r"\n\n+", r"</p>\n<p>", content)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email Digest - {markdown[:50]}...</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            max-width: 800px;
            margin: 2em auto;
            padding: 0 1em;
            line-height: 1.6;
            color: #333;
        }}
        h1, h2, h3 {{ color: #2c3e50; }}
        h1 {{ border-bottom: 2px solid #3498db; padding-bottom: 0.3em; }}
        h2 {{ border-bottom: 1px solid #bdc3c7; padding-bottom: 0.2em; }}
        li {{ margin: 0.5em 0; }}
        code {{
            background: #f4f4f4;
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-family: 'SF Mono', Consolas, monospace;
        }}
        hr {{ border: none; border-top: 1px solid #bdc3c7; margin: 2em 0; }}
        .urgent {{ color: #e74c3c; }}
        .high {{ color: #f39c12; }}
    </style>
</head>
<body>
<p>{content}</p>
</body>
</html>"""

    def _markdown_to_text(self, markdown: str) -> str:
        """Convert markdown to plain text."""
        text = markdown

        # Remove markdown formatting
        text = re.sub(r"#{1,6}\s*", "", text)  # Headers
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # Bold
        text = re.sub(r"\*([^*]+)\*", r"\1", text)  # Italic
        text = re.sub(r"`([^`]+)`", r"\1", text)  # Code
        text = re.sub(r"^\s*-\s*", "• ", text, flags=re.MULTILINE)  # Lists
        text = re.sub(r"---+", "-" * 40, text)  # Horizontal rules

        return text
