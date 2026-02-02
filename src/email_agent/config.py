"""Configuration management for email-agent."""

from pathlib import Path
from typing import Any

import yaml
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
    """Local Maildir configuration.

    The email address is the top-level key in the config. Other fields are optional:
    - account_name: derived from email domain if not set (e.g., "protonmail" from "x@protonmail.com")
    - path: defaults to ~/Mail/<email_address>
    - default: marks this as the default source when --source is omitted
    """

    # email_address is set from the config key, not from YAML content
    email_address: str = ""
    account_name: str | None = None
    path: Path | None = None
    default: bool = False

    def with_email(self, email: str) -> "MaildirConfig":
        """Return a copy with email_address set."""
        return self.model_copy(update={"email_address": email})

    @property
    def resolved_account_name(self) -> str:
        """Get account name, defaulting to email domain."""
        if self.account_name:
            return self.account_name
        # Extract domain without TLD: "foo@bar.example.com" -> "bar"
        if "@" in self.email_address:
            domain = self.email_address.split("@")[1]
            # Take first part of domain (before any dots)
            return domain.split(".")[0]
        return "local"

    @property
    def resolved_path(self) -> Path:
        """Get path, defaulting to ~/Mail/<email_address>."""
        if self.path:
            return self.path
        return Path.home() / "Mail" / self.email_address


class MXRouteConfig(BaseModel):
    """MXroute MCP integration configuration."""

    enabled: bool = False
    domain: str | None = None


class NotmuchConfig(BaseModel):
    """Notmuch integration configuration.

    NotmuchSource is the preferred email source for emma, leveraging
    notmuch's indexing and search capabilities.
    """

    enabled: bool = True  # Enabled by default if notmuch is available
    database_path: Path | None = None  # Uses default ~/.notmuch if None
    processed_tag: str = "emma-processed"  # Tag applied to processed emails
    # Default query filters (applied to all fetches)
    exclude_tags: list[str] = Field(default_factory=lambda: ["spam", "deleted"])
    # Account tags for filtering (e.g., ["gmail", "proton"])
    account_tags: list[str] = Field(default_factory=list)


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


class DigestDeliveryConfig(BaseModel):
    """Configuration for digest delivery methods."""

    type: str = "file"  # "file" only for now (email deferred)
    output_dir: Path | None = None  # Default: ~/.local/share/emma/digests/
    format: str = "markdown"  # "markdown", "html", "text"


class DigestConfig(BaseModel):
    """Configuration for email digest generation."""

    enabled: bool = True
    schedule: list[str] = Field(default_factory=lambda: ["08:00", "20:00"])  # 24h times
    period_hours: int = 12
    min_emails: int = 1
    include_action_items: bool = True
    delivery: list[DigestDeliveryConfig] = Field(default_factory=list)


class MonitorConfig(BaseModel):
    """Configuration for email monitoring."""

    enabled: bool = True
    sources: list[str] = Field(default_factory=list)  # Empty = all configured sources
    folders: list[str] = Field(default_factory=lambda: ["INBOX"])
    auto_classify: bool = True
    apply_rules: bool = True
    extract_actions: bool = True


class ActionItemConfig(BaseModel):
    """Configuration for action item extraction."""

    auto_extract: bool = True


class ServiceConfig(BaseModel):
    """Configuration for the Emma background service."""

    enabled: bool = False
    polling_interval: int = 300  # seconds (5 minutes)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    action_items: ActionItemConfig = Field(default_factory=ActionItemConfig)


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
    notmuch: NotmuchConfig = Field(default_factory=NotmuchConfig)

    # LLM settings
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Safety guardrails
    guardrails: GuardrailSettings = Field(default_factory=GuardrailSettings)

    # Processing settings
    batch_size: int = 50
    polling_interval: int = 300  # seconds

    # Service settings (emma background service)
    service: ServiceConfig = Field(default_factory=ServiceConfig)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.db_path is None:
            self.db_path = self.data_dir / "email_agent.db"

    def ensure_dirs(self) -> None:
        """Create necessary directories."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_user_email_for_source(self, source_name: str) -> str | None:
        """Get the user's email address for a given source/account name.

        Args:
            source_name: The source name (resolved_account_name, e.g., "protonmail", "gmail")

        Returns:
            The user's email address for that source, or None if not found
        """
        # Check maildir accounts by resolved_account_name
        for cfg in self.maildir_accounts.values():
            if cfg.resolved_account_name == source_name:
                return cfg.email_address

        # TODO: Add IMAP account lookup when email_address field is added
        return None

    def get_all_user_emails(self) -> list[str]:
        """Get all configured user email addresses."""
        emails = []
        for cfg in self.maildir_accounts.values():
            if cfg.email_address:
                emails.append(cfg.email_address)
        return emails

    def get_maildir_by_account_name(self, account_name: str) -> MaildirConfig | None:
        """Look up a maildir config by its resolved account name.

        Args:
            account_name: The account name (e.g., "protonmail", "work")

        Returns:
            The MaildirConfig, or None if not found
        """
        for cfg in self.maildir_accounts.values():
            if cfg.resolved_account_name == account_name:
                return cfg
        return None

    def get_default_maildir(self) -> tuple[str, MaildirConfig] | None:
        """Get the default maildir config (marked with default: true).

        Returns:
            Tuple of (account_name, config) or None if no default set.
            If no explicit default, returns the first configured account.
        """
        # Look for explicit default
        for email, cfg in self.maildir_accounts.items():
            if cfg.default:
                return (cfg.resolved_account_name, cfg)

        # Fall back to first account
        if self.maildir_accounts:
            email, cfg = next(iter(self.maildir_accounts.items()))
            return (cfg.resolved_account_name, cfg)

        return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries, with override taking precedence.

    Nested dicts are merged recursively. Lists and other values are replaced.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings() -> Settings:
    """Load settings from environment and config files.

    Loads config.yaml first (nix-managed), then merges config.local.yaml
    on top if it exists (user-editable overrides).
    """
    config_dir = Path.home() / ".config" / "emma"
    config_file = config_dir / "config.yaml"
    local_config_file = config_dir / "config.local.yaml"

    file_settings: dict[str, Any] = {}

    # Load base config (may be nix-managed/symlinked)
    if config_file.exists():
        with open(config_file) as f:
            file_settings = yaml.safe_load(f) or {}

    # Load and merge local overrides (user-editable)
    if local_config_file.exists():
        with open(local_config_file) as f:
            local_settings = yaml.safe_load(f) or {}
        file_settings = _deep_merge(file_settings, local_settings)

    # Process maildir_accounts: email address is the key, populate email_address field
    if "maildir_accounts" in file_settings:
        processed_accounts = {}
        for email_key, cfg in file_settings["maildir_accounts"].items():
            # Handle empty config (e.g., "email@example.com: {}" or "email@example.com:")
            if cfg is None:
                cfg = {}

            # Set email_address from the key
            cfg["email_address"] = email_key

            # Expand ~ in path if provided
            if "path" in cfg and isinstance(cfg["path"], str):
                cfg["path"] = Path(cfg["path"]).expanduser()

            processed_accounts[email_key] = cfg

        file_settings["maildir_accounts"] = processed_accounts

    return Settings(**file_settings)
