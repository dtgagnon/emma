"""LLM-based email processing."""

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import uuid

from email_agent.config import LLMConfig
from email_agent.models import DraftReply, DraftStatus, Email, EmailCategory, EmailPriority
from email_agent.utils.text import prepare_body


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], max_tokens: int, temperature: float) -> str:
        """Send a chat completion request and return the response text."""
        ...


class AnthropicClient(LLMClient):
    """Anthropic API client."""

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def chat(self, messages: list[dict[str, str]], max_tokens: int, temperature: float) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        return response.content[0].text


class OllamaClient(LLMClient):
    """Ollama client using native ollama library."""

    def __init__(self, base_url: str, model: str, context_length: int = 8192) -> None:
        import ollama

        self.client = ollama.Client(host=base_url)
        self.model = model
        self.context_length = context_length

    def chat(self, messages: list[dict[str, str]], max_tokens: int, temperature: float) -> str:
        import time

        # Retry logic to handle transient empty responses (e.g., model warmup)
        max_retries = 2
        for attempt in range(max_retries + 1):
            response = self.client.chat(
                model=self.model,
                messages=messages,  # type: ignore
                options={
                    "num_ctx": self.context_length,
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            )
            content = response["message"]["content"] or ""
            if content.strip():
                return content
            elif attempt < max_retries:
                time.sleep(0.3)  # Brief pause before retry

        return content  # Return whatever we got on last attempt


def create_llm_client(config: LLMConfig, api_key: str | None = None) -> LLMClient:
    """Factory function to create the appropriate LLM client."""
    if config.provider == "anthropic":
        if not api_key:
            raise ValueError("Anthropic API key required")
        return AnthropicClient(api_key=api_key, model=config.model)
    elif config.provider == "ollama":
        return OllamaClient(
            base_url=config.ollama_base_url,
            model=config.model,
            context_length=config.ollama_context_length,
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
        self._user_email_lookup = user_email_lookup

    def _chat(self, prompt: str, max_tokens: int | None = None, temperature: float | None = None) -> str:
        """Send a chat message and get the response."""
        return self.client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature if temperature is not None else self.config.temperature,
        )

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

        raise ValueError(f"Could not parse JSON from response: {text[:200]}")

    async def analyze_email(self, email: Email) -> dict[str, Any]:
        """Perform comprehensive analysis of an email.

        Returns analysis including:
        - category: Email category classification
        - priority: Urgency/priority level
        - summary: Brief summary of the email
        - sentiment: Detected sentiment (positive, negative, neutral)
        - action_required: Whether a response/action is needed
        - suggested_tags: Relevant tags for organization
        - key_points: Main points from the email
        """
        context = self._build_email_context(email, "analyze")
        user_email = self._get_user_email(email)

        # Build perspective-aware instructions
        if user_email:
            perspective_hint = f"""
The user's email address is shown in brackets above. Use it to determine:
- If From matches the user's address: the user SENT this email (action_required=false, no suggested_response needed)
- If To/CC contains the user's address: the user RECEIVED this email (evaluate if action is needed)
- Is the user the primary recipient (To) or just CC'd? CC'd recipients typically don't need to respond."""
        else:
            perspective_hint = """
- Are you the primary recipient (To) or just CC'd? This affects action_required.
- Is this a direct message or a broadcast to many recipients?"""

        prompt = f"""Analyze this email and provide a structured analysis.

{context}

Consider:{perspective_hint}
- Does the date suggest any urgency?

Provide your analysis as JSON with these fields:
- category: one of "personal", "work", "newsletter", "promotional", "transactional", "spam", "other"
- priority: one of "low", "normal", "high", "urgent"
- summary: brief 1-2 sentence summary
- sentiment: "positive", "negative", or "neutral"
- action_required: boolean, whether the user needs to respond or take action
- suggested_tags: list of relevant tags (max 5)
- key_points: list of main points (max 3)
- suggested_response: if action_required is true, brief suggestion for how the user should respond

Return ONLY valid JSON, no other text."""

        response = self._chat(prompt)

        try:
            result = self._parse_json(response)
            return result if isinstance(result, dict) else {"error": "Expected object", "raw": response}
        except ValueError:
            return {"error": "Failed to parse LLM response", "raw": response}

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

        response = self._chat(prompt, max_tokens=150, temperature=0.1)

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
        except (ValueError, KeyError):
            pass

        return EmailCategory.OTHER, EmailPriority.NORMAL

    async def summarize_email(self, email: Email) -> str:
        """Generate a brief summary of an email."""
        context = self._build_email_context(email, "summarize")
        prompt = f"""Summarize this email in 1-2 sentences.

{context}

Summary:"""

        return self._chat(prompt, max_tokens=150, temperature=0.3).strip()

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

        draft_body = self._chat(prompt, max_tokens=500, temperature=0.7).strip()

        return DraftReply(
            id=str(uuid.uuid4()),
            original_email_id=email.id,
            original_subject=email.subject,
            recipient=email.from_addr,
            draft_body=draft_body,
            status=DraftStatus.PENDING_REVIEW,
            instructions=instructions or None,
        )

    async def extract_action_items(self, email: Email) -> list[str]:
        """Extract action items or tasks from an email."""
        context = self._build_email_context(email, "extract_actions")
        prompt = f"""Extract action items from this email. List specific tasks that need to be done.

{context}

Consider:
- Who is being asked to do something? (Check To/CC fields)
- Are there deadlines or time-sensitive requests?
- What concrete actions are requested?

Return JSON array of action items (strings). Return [] if none found."""

        response = self._chat(prompt, max_tokens=300, temperature=0)

        try:
            result = self._parse_json(response)
            return result if isinstance(result, list) else []
        except ValueError:
            return []
