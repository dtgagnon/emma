"""LLM-based email processing."""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import uuid

from email_agent.config import LLMConfig
from email_agent.models import DraftReply, DraftStatus, Email, EmailCategory, EmailPriority
from email_agent.utils.text import prepare_body

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        """Send a chat completion request and return the response text."""
        ...


class AnthropicClient(LLMClient):
    """Anthropic API client."""

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        logger.info(f"Initialized Anthropic client: model={model}")

    def chat(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        start = time.monotonic()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=messages,
        )
        elapsed = time.monotonic() - start
        logger.debug(f"Anthropic chat completed in {elapsed:.1f}s (model={self.model})")
        return response.content[0].text


class OllamaClient(LLMClient):
    """Ollama client using native ollama library."""

    def __init__(self, base_url: str, model: str, context_length: int = 8192) -> None:
        import ollama

        self.client = ollama.Client(host=base_url)
        self.model = model
        self.context_length = context_length
        logger.info(f"Initialized Ollama client: model={model}, base_url={base_url}, ctx={context_length}")

    def chat(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        # Retry logic to handle transient empty responses (e.g., model warmup)
        max_retries = 2
        for attempt in range(max_retries + 1):
            start = time.monotonic()
            response = self.client.chat(
                model=self.model,
                messages=messages,  # type: ignore
                options={
                    "num_ctx": self.context_length,
                    "num_predict": max_tokens,
                },
            )
            elapsed = time.monotonic() - start
            content = response["message"]["content"] or ""
            if content.strip():
                logger.debug(f"Ollama chat completed in {elapsed:.1f}s (model={self.model})")
                return content
            elif attempt < max_retries:
                logger.warning(f"Ollama returned empty response (attempt {attempt + 1}/{max_retries + 1}), retrying...")
                time.sleep(0.3)  # Brief pause before retry

        logger.warning(f"Ollama returned empty response after {max_retries + 1} attempts (model={self.model})")
        return content  # Return whatever we got on last attempt


class OpenAICompatibleClient(LLMClient):
    """Client for OpenAI-compatible APIs (OpenAI, vLLM, LiteLLM, etc.)."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        import openai

        self.client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
        )
        self.model = model
        logger.info(f"Initialized OpenAI-compatible client: model={model}, base_url={base_url}")

    def chat(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        start = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore
            max_tokens=max_tokens,
        )
        elapsed = time.monotonic() - start
        logger.debug(f"OpenAI-compatible chat completed in {elapsed:.1f}s (model={self.model})")
        return response.choices[0].message.content or ""


def create_llm_client(config: LLMConfig, api_key: str | None = None) -> LLMClient:
    """Factory function to create the appropriate LLM client."""
    logger.debug(f"Creating LLM client: provider={config.provider}, model={config.model}")
    if config.provider == "anthropic":
        if not api_key:
            raise ValueError("Anthropic API key required")
        return AnthropicClient(api_key=api_key, model=config.model)
    elif config.provider == "ollama":
        return OllamaClient(
            base_url=config.base_url,
            model=config.model,
            context_length=config.context_length,
        )
    elif config.provider == "openai":
        return OpenAICompatibleClient(
            base_url=config.base_url,
            model=config.model,
            api_key=api_key,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {config.provider}")


class LLMProcessor:
    """Process emails using LLM for classification, summarization, and analysis."""

    def __init__(
        self,
        config: LLMConfig,
        api_key: str | None = None,
        user_email_lookup: "Callable[[str], str | None] | None" = None,
    ) -> None:
        """Initialize the LLM processor.

        Args:
            config: LLM configuration
            api_key: API key for Anthropic (if using that provider)
            user_email_lookup: Optional callback to get user's email for a source name.
                               Called with source name, returns user's email address or None.
        """
        self.config = config
        self.client = create_llm_client(config, api_key)
        self._api_key = api_key
        self._user_email_lookup = user_email_lookup
        self._task_clients: dict[str, tuple[LLMClient, LLMConfig]] = {}

    def _get_task_client(self, task: str) -> tuple[LLMClient, LLMConfig]:
        """Get the LLM client and config for a specific task, using overrides if configured."""
        if task not in self._task_clients:
            resolved = self.config.resolve_for_task(task)
            if resolved is self.config:
                self._task_clients[task] = (self.client, self.config)
            else:
                self._task_clients[task] = (create_llm_client(resolved, self._api_key), resolved)
        return self._task_clients[task]

    def _chat(self, prompt: str, max_tokens: int | None = None, task: str | None = None) -> str:
        """Send a chat message and get the response."""
        if task:
            client, config = self._get_task_client(task)
        else:
            client, config = self.client, self.config
        tokens = max_tokens or config.max_tokens
        logger.debug(f"LLM request: task={task}, model={config.model}, max_tokens={tokens}, prompt_len={len(prompt)}")
        start = time.monotonic()
        result = client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=tokens,
        )
        elapsed = time.monotonic() - start
        logger.info(f"LLM response: task={task}, model={config.model}, {elapsed:.1f}s, response_len={len(result)}")
        return result

    def _get_user_email(self, email: Email) -> str | None:
        """Get the user's email address for the account that received this email."""
        if self._user_email_lookup:
            return self._user_email_lookup(email.source)
        return None

    def _build_email_context(self, email: Email, task: str) -> str:
        """Build task-appropriate context string for LLM processing.

        Different tasks need different context:
        - classify: From domain, subject, body preview
        - analyze: Full headers, recipients (CC context matters), full body
        - summarize: From, subject, full body
        - extract_actions: Full headers with recipients, date, full body
        - draft_reply: From, subject, full body
        - priority: Recipients (direct vs CC), date, subject, body preview

        Args:
            email: The email to build context for
            task: One of "classify", "analyze", "summarize",
                  "extract_actions", "draft_reply", "priority"

        Returns:
            Formatted context string optimized for the task
        """
        parts = []

        # User identity context - helps LLM understand perspective
        user_email = self._get_user_email(email)
        if user_email and task in ("analyze", "extract_actions", "priority", "draft_reply"):
            parts.append(f"[User's email: {user_email}]")

        # From address - always include but simplify for some tasks
        if task == "classify":
            # Just domain is often enough for classification
            from_addr = email.from_addr
            if "@" in from_addr:
                # Extract domain for simpler context
                parts.append(f"From: {from_addr}")
            else:
                parts.append(f"From: {from_addr}")
        else:
            parts.append(f"From: {email.from_addr}")

        # To/CC - important for determining if user is primary recipient or CC'd
        if task in ("analyze", "extract_actions", "priority"):
            if email.to_addrs:
                parts.append(f"To: {', '.join(email.to_addrs)}")
            if email.cc_addrs:
                parts.append(f"CC: {', '.join(email.cc_addrs)}")

        # Date - important for urgency context and action items
        if task in ("analyze", "extract_actions", "priority"):
            if email.date:
                parts.append(f"Date: {email.date}")

        # Subject - always include
        parts.append(f"Subject: {email.subject}")

        # Body - prepared appropriately for task
        body = prepare_body(email.body_text, task)
        parts.append(f"\nBody:\n{body}")

        return "\n".join(parts)

    def _parse_json(self, text: str) -> dict[str, Any] | list[Any]:
        """Parse JSON from LLM response, handling markdown code blocks."""
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract from markdown code block
        code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object or array
        json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse JSON from LLM response: {text[:200]}")
        raise ValueError(f"Could not parse JSON from response: {text[:200]}")

    async def analyze_email(self, email: Email) -> dict[str, Any]:
        """Analyze an email: generate a summary and extract action items.

        Returns dict with:
        - summary: 1-2 sentence plain text summary
        - action_items: list of structured action item dicts
        """
        context = self._build_email_context(email, "analyze")
        user_email = self._get_user_email(email)

        user_context = ""
        if user_email:
            user_context = f"\nYou (the recipient): {user_email}"

        to_field = ", ".join(email.to_addrs) if email.to_addrs else "(unknown)"

        prompt = f"""Analyze this email. Provide a brief summary and extract any action items.

{context}{user_context}

Return a JSON object with:
- summary: 1-2 sentence summary of the email's content and purpose
- action_items: array of action items (empty array if none found)

For each action item, include:
- title: concise action item title (required)
- description: fuller description if needed
- priority: low, normal, high, or urgent
- urgency: low, normal, high, or urgent (how time-sensitive)
- due_date: ISO date if mentioned/implied (YYYY-MM-DD), null if not
- confidence: 0.0-1.0 how confident this is a real action item
- relevance: "direct" if someone is personally asking the recipient to do something, "informational" if it is a general announcement, newsletter CTA, or FYI

Guidelines for relevance:
- "direct": the sender explicitly asks the recipient to take a specific action (reply, review, schedule, submit, etc.)
- "informational": generic calls to action (click here, shop now, learn more), announcements, or actions mentioned in passing

Example response:
{{"summary": "Meeting reminder from Bob for Thursday at 2pm to discuss Q2 planning.", "action_items": [{{"title": "Attend Q2 planning meeting", "priority": "normal", "urgency": "high", "due_date": null, "confidence": 0.9, "relevance": "direct"}}]}}

Return ONLY valid JSON, no other text."""

        response = self._chat(prompt, task="analyze")

        try:
            result = self._parse_json(response)
            if isinstance(result, dict):
                # Ensure expected fields exist
                result.setdefault("summary", "")
                result.setdefault("action_items", [])
                return result
            return {"summary": "", "action_items": [], "error": "Expected object"}
        except ValueError as e:
            logger.warning(f"Analysis parse failed for {email.id}: {e}")
            return {"summary": "", "action_items": [], "error": "Failed to parse LLM response"}

    async def classify_email(self, email: Email) -> tuple[EmailCategory, EmailPriority]:
        """Quick classification of email category and priority."""
        context = self._build_email_context(email, "classify")
        prompt = f"""Classify this email. Respond with JSON only.

{context}

Categories (choose ONE - prefer specific categories over "other"):
- personal: Health/medical providers, therapy, personal finances (bank statements, credit cards), personal appointments, vehicle/car related, personal account security (login links, 2FA), hobbies, casual communications
- work_clients: Direct communications from/about business clients
- work_admin: Internal work admin, team updates, HR, IT, support tickets for work tools
- newsletter: Subscribed newsletters, digests, regular content emails
- promotional: Marketing, sales, deals, giveaways, sweepstakes, cashback offers, "running out" urgency tactics, job postings from Indeed/LinkedIn/job sites
- spam: Unwanted, suspicious, phishing
- other: ONLY if absolutely none of the above fit

Classification tips:
- "cashback", "giveaway", "running out", "limited time" → promotional
- Doctor/medical appointments, therapy → personal (urgent if soon)
- Car diagnostics, vehicle reports → personal
- Login/security links for personal accounts (Claude.ai, etc.) → personal
- Job postings from job sites → promotional (not work)
- Invoices for coworking/office space → work_admin (unless for personal use)

Return JSON:
{{"category": "<personal|work_clients|work_admin|newsletter|promotional|spam|other>", "priority": "<low|normal|high|urgent>"}}"""

        response = self._chat(prompt, task="classify")

        try:
            result = self._parse_json(response)
            if isinstance(result, dict):
                raw_category = result.get("category", "other")
                # Map legacy/variant categories to valid enum values
                category_map = {
                    "work": "work_admin",
                    "transactional": "personal",
                    "miscellaneous": "other",
                }
                mapped = category_map.get(raw_category, raw_category)
                category = EmailCategory(mapped)
                priority = EmailPriority(result.get("priority", "normal"))
                return category, priority
        except (ValueError, KeyError) as e:
            logger.warning(f"Classification parse failed for {email.id}, defaulting to OTHER/NORMAL: {e}")

        return EmailCategory.OTHER, EmailPriority.NORMAL

    async def draft_reply(self, email: Email, instructions: str = "") -> DraftReply:
        """Draft a reply to an email.

        This method creates a draft that MUST be reviewed and approved by the user
        before sending. Automated sending is never performed.

        Args:
            email: The email to reply to
            instructions: Optional instructions for the reply tone/content

        Returns:
            DraftReply object with status=PENDING_REVIEW, requiring user approval
        """
        context = self._build_email_context(email, "draft_reply")
        prompt = f"""Draft a reply to this email.

Original email:
{context}

{f"Instructions: {instructions}" if instructions else "Write a professional, helpful reply."}

Draft reply (body only, no subject line or headers):"""

        draft_body = self._chat(prompt, max_tokens=500).strip()

        return DraftReply(
            id=str(uuid.uuid4()),
            original_email_id=email.id,
            original_subject=email.subject,
            recipient=email.from_addr,
            draft_body=draft_body,
            status=DraftStatus.PENDING_REVIEW,
            instructions=instructions or None,
        )

