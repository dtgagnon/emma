"""Tests for text processing utilities."""

import pytest

from email_agent.utils.text import (
    collapse_whitespace,
    prepare_body,
    smart_truncate,
    strip_mobile_footers,
    strip_quoted_replies,
)


class TestStripMobileFooters:
    """Tests for strip_mobile_footers function."""

    def test_strips_iphone_footer(self):
        text = "Hello!\n\nSent from my iPhone"
        result = strip_mobile_footers(text)
        assert "Sent from my iPhone" not in result
        assert "Hello!" in result

    def test_strips_outlook_footer(self):
        text = "Thanks!\nGet Outlook for iOS"
        result = strip_mobile_footers(text)
        assert "Get Outlook" not in result
        assert "Thanks!" in result

    def test_preserves_normal_text(self):
        text = "I sent this from my computer.\nRegards"
        result = strip_mobile_footers(text)
        assert result == text

    def test_case_insensitive(self):
        text = "Hi\nSENT FROM MY IPHONE"
        result = strip_mobile_footers(text)
        assert "IPHONE" not in result


class TestStripQuotedReplies:
    """Tests for strip_quoted_replies function."""

    def test_strips_quoted_lines(self):
        text = "My reply.\n\n> Original message\n> More original"
        result = strip_quoted_replies(text)
        assert "My reply" in result
        assert "> Original" not in result

    def test_strips_on_wrote_header(self):
        text = "Thanks for the update.\n\nOn Mon, Jan 1, 2024, John wrote:\n> Old content"
        result = strip_quoted_replies(text)
        assert "Thanks for the update" in result
        assert "John wrote" not in result
        assert "Old content" not in result

    def test_preserves_content_before_quotes(self):
        text = "Line 1\nLine 2\nLine 3\n\n> Quoted stuff"
        result = strip_quoted_replies(text)
        assert "Line 1" in result
        assert "Line 2" in result
        assert "Line 3" in result

    def test_strips_outlook_separator(self):
        text = "My response.\n\n_____________\nFrom: someone"
        result = strip_quoted_replies(text)
        assert "My response" in result
        assert "_____" not in result


class TestSmartTruncate:
    """Tests for smart_truncate function."""

    def test_no_truncation_needed(self):
        text = "Short text."
        result = smart_truncate(text, max_chars=100)
        assert result == text

    def test_truncates_at_sentence(self):
        text = "First sentence. Second sentence. Third sentence."
        result = smart_truncate(text, max_chars=30, at_sentence=True)
        assert result == "First sentence."

    def test_truncates_at_word_if_no_sentence(self):
        text = "One very long sentence without period until the end here"
        result = smart_truncate(text, max_chars=30, at_sentence=True)
        assert result.endswith("...")
        assert len(result) <= 30

    def test_hard_truncation(self):
        text = "A" * 100
        result = smart_truncate(text, max_chars=50, at_sentence=False)
        assert len(result) == 50
        assert result.endswith("...")


class TestCollapseWhitespace:
    """Tests for collapse_whitespace function."""

    def test_collapses_multiple_spaces(self):
        text = "Hello    world"
        result = collapse_whitespace(text)
        assert result == "Hello world"

    def test_collapses_excessive_newlines(self):
        text = "Para 1\n\n\n\n\nPara 2"
        result = collapse_whitespace(text)
        assert result == "Para 1\n\nPara 2"

    def test_strips_line_whitespace(self):
        text = "  Line 1  \n  Line 2  "
        result = collapse_whitespace(text)
        assert result == "Line 1\nLine 2"


class TestPrepareBody:
    """Tests for prepare_body function."""

    def test_classify_task_aggressive_truncation(self):
        text = "A" * 1000
        result = prepare_body(text, "classify")
        assert len(result) <= 503  # 500 + "..."

    def test_analyze_task_preserves_more(self):
        text = "Important content. " * 100
        result = prepare_body(text, "analyze")
        # Should have more content than classify
        assert len(result) > 500

    def test_always_strips_mobile_footer(self):
        text = "Content here.\n\nSent from my iPhone"
        for task in ["classify", "analyze", "summarize", "extract_actions", "draft_reply"]:
            result = prepare_body(text, task)
            assert "iPhone" not in result

    def test_draft_reply_keeps_more_context(self):
        # draft_reply doesn't strip quoted replies as aggressively
        # since context is needed for appropriate response
        quoted = "> Previous message content"
        text = f"My reply.\n\n{quoted}"
        result = prepare_body(text, "draft_reply")
        # draft_reply should not strip quoted content (needs context)
        assert ">" in result or "Previous" in result

    def test_extract_actions_strips_quotes(self):
        text = "Action: Review document.\n\n> Old thread content"
        result = prepare_body(text, "extract_actions")
        assert "Review document" in result
        assert "> Old" not in result

    def test_unknown_task_uses_defaults(self):
        text = "Some content here."
        result = prepare_body(text, "unknown_task")
        assert "Some content" in result
