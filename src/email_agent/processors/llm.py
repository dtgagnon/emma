"""LLM-based email processing."""

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import uuid

from email_agent.config import LLMConfig
from email_agent.models import DraftReply, DraftStatus, Email, EmailCategory, EmailPriority


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
        response = self.client.chat(
            model=self.model,
            messages=messages,  # type: ignore
            options={
                "num_ctx": self.context_length,
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        )
        return response["message"]["content"] or ""


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

    def __init__(self, config: LLMConfig, api_key: str | None = None) -> None:
        self.config = config
        self.client = create_llm_client(config, api_key)

    def _chat(self, prompt: str, max_tokens: int | None = None, temperature: float | None = None) -> str:
        """Send a chat message and get the response."""
        return self.client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature if temperature is not None else self.config.temperature,
        )

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
        prompt = f"""Analyze this email and provide a structured analysis.

From: {email.from_addr}
To: {', '.join(email.to_addrs)}
Subject: {email.subject}
Date: {email.date}

Body:
{email.body_text[:4000]}

Provide your analysis as JSON with these fields:
- category: one of "personal", "work", "newsletter", "promotional", "transactional", "spam", "other"
- priority: one of "low", "normal", "high", "urgent"
- summary: brief 1-2 sentence summary
- sentiment: "positive", "negative", or "neutral"
- action_required: boolean, whether a response or action is needed
- suggested_tags: list of relevant tags (max 5)
- key_points: list of main points (max 3)
- suggested_response: if action_required is true, brief suggestion for response

Return ONLY valid JSON, no other text."""

        response = self._chat(prompt)

        try:
            result = self._parse_json(response)
            return result if isinstance(result, dict) else {"error": "Expected object", "raw": response}
        except ValueError:
            return {"error": "Failed to parse LLM response", "raw": response}

    async def classify_email(self, email: Email) -> tuple[EmailCategory, EmailPriority]:
        """Quick classification of email category and priority."""
        prompt = f"""Classify this email. Respond with JSON only.

From: {email.from_addr}
Subject: {email.subject}
Body preview: {email.body_text[:500]}

Return JSON:
{{"category": "<personal|work|newsletter|promotional|transactional|spam|other>", "priority": "<low|normal|high|urgent>"}}"""

        response = self._chat(prompt, max_tokens=100, temperature=0)

        try:
            result = self._parse_json(response)
            if isinstance(result, dict):
                category = EmailCategory(result.get("category", "other"))
                priority = EmailPriority(result.get("priority", "normal"))
                return category, priority
        except (ValueError, KeyError):
            pass

        return EmailCategory.OTHER, EmailPriority.NORMAL

    async def summarize_email(self, email: Email) -> str:
        """Generate a brief summary of an email."""
        prompt = f"""Summarize this email in 1-2 sentences.

From: {email.from_addr}
Subject: {email.subject}
Body:
{email.body_text[:3000]}

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
        prompt = f"""Draft a reply to this email.

Original email:
From: {email.from_addr}
Subject: {email.subject}
Body:
{email.body_text[:3000]}

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
        prompt = f"""Extract action items from this email. List specific tasks that need to be done.

From: {email.from_addr}
Subject: {email.subject}
Body:
{email.body_text[:3000]}

Return JSON array of action items (strings). Return [] if none found."""

        response = self._chat(prompt, max_tokens=300, temperature=0)

        try:
            result = self._parse_json(response)
            return result if isinstance(result, list) else []
        except ValueError:
            return []
