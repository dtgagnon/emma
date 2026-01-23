"""Configuration management for email-agent."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IMAPConfig(BaseModel):
    """IMAP server configuration."""

    host: str
    port: int = 993
    username: str
    password: str
    use_ssl: bool = True
    folders: list[str] = Field(default_factory=lambda: ["INBOX"])


class SMTPConfig(BaseModel):
    """SMTP server configuration."""

    host: str
    port: int = 587
    username: str
    password: str
    use_tls: bool = True


class MaildirConfig(BaseModel):
    """Local Maildir configuration."""

    path: Path
    account_name: str = "local"


class MXRouteConfig(BaseModel):
    """MXroute MCP integration configuration."""

    enabled: bool = False
    domain: str | None = None


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "ollama"  # "anthropic" or "ollama"
    model: str = "gpt-oss:20b"  # Ollama model name or Anthropic model ID
    max_tokens: int = 1024
    temperature: float = 0.3
    # Ollama settings
    ollama_base_url: str = "http://localhost:11434"
    ollama_context_length: int = 24576  # num_ctx for Ollama models (24k default)


class ReplySettings(BaseModel):
    """Settings for automated reply handling."""

    mode: str = "draft_only"  # Only supported mode - replies always go to drafts
    drafts_folder: str = "Drafts"


class GuardrailSettings(BaseModel):
    """Safety guardrails for email operations."""

    # Dry-run mode - preview changes without applying them
    dry_run_by_default: bool = True

    # Soft delete - move to Trash instead of permanent delete
    soft_delete: bool = True
    trash_folder: str = "Trash"

    # Reply safety - always create drafts, never auto-send
    reply: ReplySettings = Field(default_factory=ReplySettings)

    # Audit logging
    audit_enabled: bool = True
    audit_db_name: str = "audit.db"


class Settings(BaseSettings):
    """Application settings loaded from environment and config files."""

    model_config = SettingsConfigDict(
        env_prefix="EMMA_",
        env_nested_delimiter="__",
    )

    # Paths
    config_dir: Path = Field(default_factory=lambda: Path.home() / ".config" / "emma")
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".local" / "share" / "emma")
    db_path: Path | None = None

    # API keys (loaded from environment)
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Email sources
    imap_accounts: dict[str, IMAPConfig] = Field(default_factory=dict)
    smtp_accounts: dict[str, SMTPConfig] = Field(default_factory=dict)
    maildir_accounts: dict[str, MaildirConfig] = Field(default_factory=dict)
    mxroute: MXRouteConfig = Field(default_factory=MXRouteConfig)

    # LLM settings
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Safety guardrails
    guardrails: GuardrailSettings = Field(default_factory=GuardrailSettings)

    # Processing settings
    batch_size: int = 50
    polling_interval: int = 300  # seconds

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.db_path is None:
            self.db_path = self.data_dir / "email_agent.db"

    def ensure_dirs(self) -> None:
        """Create necessary directories."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Load settings from environment and config files."""
    return Settings()
