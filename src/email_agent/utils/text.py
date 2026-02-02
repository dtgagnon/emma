"""Text processing utilities for email content preparation.

Provides functions to clean, truncate, and prepare email body text
for LLM processing with task-appropriate context.
"""

import html
import re


def html_to_text(html_content: str) -> str:
    """Convert HTML to plain text.

    Strips tags, decodes entities, and aggressively collapses whitespace
    to minimize token usage for LLM processing.

    Args:
        html_content: Raw HTML string

    Returns:
        Clean plain text extracted from HTML
    """
    # Remove script and style elements
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html_content, flags=re.IGNORECASE)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)

    # Remove HTML comments
    text = re.sub(r"<!--[\s\S]*?-->", "", text)

    # Replace common block elements with newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</td>", " | ", text, flags=re.IGNORECASE)

    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities
    text = html.unescape(text)

    # Aggressive whitespace cleanup
    text = re.sub(r"[ \t]+", " ", text)  # Collapse horizontal whitespace
    text = "\n".join(line.strip() for line in text.splitlines())  # Strip each line
    text = re.sub(r"\n{2,}", "\n\n", text)  # Max 1 blank line between paragraphs
    text = re.sub(r"^\n+", "", text)  # Remove leading newlines
    text = re.sub(r"\n+$", "", text)  # Remove trailing newlines

    # Remove lines that are only whitespace or punctuation (common in HTML email cruft)
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not re.match(r"^[\s|_\-=]+$", line)
    ]

    return "\n".join(lines)


# Mobile app footer patterns to always strip
MOBILE_FOOTER_PATTERNS = [
    r"^Sent from my iPhone\s*$",
    r"^Sent from my iPad\s*$",
    r"^Sent from my Galaxy\s*$",
    r"^Sent from my Samsung\s*$",
    r"^Sent from my Android\s*$",
    r"^Get Outlook for iOS\s*$",
    r"^Get Outlook for Android\s*$",
    r"^Sent from Outlook for iOS\s*$",
    r"^Sent from Outlook for Android\s*$",
    r"^Sent from Mail for Windows\s*$",
    r"^Sent from Yahoo Mail\s*$",
    r"^Sent from AOL Mobile Mail\s*$",
]

# Quoted reply header patterns
QUOTED_HEADER_PATTERNS = [
    r"^On .+wrote:\s*$",  # "On Mon, Jan 1, 2024, Person wrote:"
    r"^-+\s*Original Message\s*-+\s*$",  # "--- Original Message ---"
    r"^From:.+\nSent:.+\nTo:.+\nSubject:",  # Outlook-style headers
    r"^_{10,}\s*$",  # Outlook separators (underscores)
]


def strip_mobile_footers(text: str) -> str:
    """Remove mobile app footers from email text.

    These footers add no semantic value and waste tokens:
    - "Sent from my iPhone"
    - "Get Outlook for iOS"
    - etc.
    """
    lines = text.splitlines()
    filtered = []

    for line in lines:
        is_footer = any(
            re.match(pattern, line.strip(), re.IGNORECASE)
            for pattern in MOBILE_FOOTER_PATTERNS
        )
        if not is_footer:
            filtered.append(line)

    return "\n".join(filtered)


def strip_quoted_replies(text: str) -> str:
    """Remove quoted reply content from email text.

    Strips:
    - Lines starting with '>' (quoted text)
    - "On date, person wrote:" headers
    - Outlook-style reply headers and separators

    Keep the original message content above the quotes.
    """
    lines = text.splitlines()
    result = []
    in_quoted_section = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Check for quoted reply headers that signal start of quoted section
        is_quote_header = any(
            re.match(pattern, stripped, re.IGNORECASE | re.DOTALL)
            for pattern in QUOTED_HEADER_PATTERNS
        )

        # Check if line is a quote (starts with >)
        is_quoted_line = stripped.startswith(">")

        # Outlook-style separator
        is_separator = re.match(r"^_{5,}$", stripped)

        if is_quote_header or is_separator:
            in_quoted_section = True
            continue

        if in_quoted_section:
            # Once in quoted section, skip until we potentially exit
            # In practice, quoted content usually goes to the end
            continue

        if is_quoted_line:
            # Skip inline quotes but don't enter quoted section mode
            continue

        result.append(line)

    return "\n".join(result)


def smart_truncate(text: str, max_chars: int, at_sentence: bool = True) -> str:
    """Truncate text intelligently at sentence boundary if possible.

    Args:
        text: The text to truncate
        max_chars: Maximum character length
        at_sentence: If True, try to truncate at a sentence boundary

    Returns:
        Truncated text, with "..." appended if truncation occurred
    """
    if len(text) <= max_chars:
        return text

    if not at_sentence:
        return text[: max_chars - 3].rstrip() + "..."

    # Try to find a sentence boundary before max_chars
    # Look for .!? followed by space or end
    truncated = text[:max_chars]

    # Find last sentence boundary
    sentence_ends = []
    for match in re.finditer(r"[.!?](?:\s|$)", truncated):
        sentence_ends.append(match.end())

    if sentence_ends:
        # Truncate at last complete sentence
        last_end = sentence_ends[-1]
        # Only use it if we're keeping at least half the allowed length
        if last_end >= max_chars // 2:
            return text[:last_end].rstrip()

    # No good sentence boundary, truncate at word boundary
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        return text[:last_space].rstrip() + "..."

    # Fall back to hard truncation
    return text[: max_chars - 3].rstrip() + "..."


def collapse_whitespace(text: str) -> str:
    """Collapse excessive whitespace while preserving paragraph structure.

    - Collapses multiple spaces to single space
    - Collapses more than 2 consecutive newlines to 2
    - Strips leading/trailing whitespace from lines
    """
    # Collapse horizontal whitespace
    text = re.sub(r"[ \t]+", " ", text)

    # Strip each line
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)

    # Collapse excessive blank lines (max 1 blank line between paragraphs)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def prepare_body(text: str, task: str) -> str:
    """Prepare email body text for a specific LLM task.

    Different tasks need different levels of cleaning and truncation:
    - classify: Aggressive truncation, minimal context needed
    - analyze: Full content, keep structure
    - summarize: Full content for accurate summary
    - extract_actions: Full content, quoted replies stripped
    - draft_reply: Full content needed for appropriate response
    - priority: Preview only, urgency cues matter

    Args:
        text: Raw email body text
        task: One of "classify", "analyze", "summarize",
              "extract_actions", "draft_reply", "priority"

    Returns:
        Cleaned and appropriately truncated text
    """
    # Always strip mobile footers - zero semantic value
    text = strip_mobile_footers(text)

    # Always collapse whitespace
    text = collapse_whitespace(text)

    # Task-specific processing
    if task == "classify":
        # Quick categorization needs minimal context
        # Strip quoted replies, aggressive truncation
        text = strip_quoted_replies(text)
        text = smart_truncate(text, max_chars=500, at_sentence=True)

    elif task == "priority":
        # Priority assessment uses preview + subject/sender cues
        text = strip_quoted_replies(text)
        text = smart_truncate(text, max_chars=800, at_sentence=True)

    elif task == "analyze":
        # Full analysis needs most content but can skip deep quote chains
        text = strip_quoted_replies(text)
        text = smart_truncate(text, max_chars=4000, at_sentence=True)

    elif task == "summarize":
        # Summary needs full content for accuracy
        text = strip_quoted_replies(text)
        text = smart_truncate(text, max_chars=4000, at_sentence=True)

    elif task == "extract_actions":
        # Action items are in the latest message, not quotes
        text = strip_quoted_replies(text)
        text = smart_truncate(text, max_chars=3500, at_sentence=True)

    elif task == "draft_reply":
        # Need full context to draft appropriate response
        # Keep some quoted context for reference
        text = smart_truncate(text, max_chars=3500, at_sentence=True)

    else:
        # Unknown task, apply reasonable defaults
        text = strip_quoted_replies(text)
        text = smart_truncate(text, max_chars=3000, at_sentence=True)

    return text
