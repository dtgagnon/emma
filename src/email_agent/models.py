"""Core data models for email processing."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EmailPriority(str, Enum):
    """Email priority levels."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class EmailCategory(str, Enum):
    """Email categories for classification."""

    # Core categories for digest display
    PERSONAL = "personal"  # Health, finances, personal interests, appointments
    WORK_CLIENTS = "work_clients"  # Client communications
    WORK_ADMIN = "work_admin"  # Internal work, admin tasks
    OTHER = "other"  # Catch-all for relevant but uncategorized

    # Filtered categories (excluded from digest)
    NEWSLETTER = "newsletter"
    PROMOTIONAL = "promotional"
    SPAM = "spam"


class Email(BaseModel):
    """Represents an email message."""

    id: str
    source: str  # Which connector provided this email
    message_id: str | None = None
    subject: str = ""
    from_addr: str = ""
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    date: datetime | None = None
    body_text: str = ""
    body_html: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    folder: str = "INBOX"
    flags: list[str] = Field(default_factory=list)
    attachments: list["Attachment"] = Field(default_factory=list)

    # Computed/enriched fields
    category: EmailCategory | None = None
    priority: EmailPriority | None = None
    summary: str | None = None
    sentiment: str | None = None
    action_required: bool = False
    tags: list[str] = Field(default_factory=list)


class Attachment(BaseModel):
    """Email attachment metadata."""

    filename: str
    content_type: str
    size: int
    content_id: str | None = None


class PlannedAction(BaseModel):
    """A planned action from dry-run mode."""

    rule_id: str
    rule_name: str
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""  # Human-readable description


class ProcessingResult(BaseModel):
    """Result of processing an email through the automation pipeline."""

    email_id: str
    email_subject: str = ""  # For display in dry-run output
    processed_at: datetime = Field(default_factory=datetime.now)
    actions_taken: list[str] = Field(default_factory=list)
    planned_actions: list[PlannedAction] = Field(default_factory=list)  # For dry-run mode
    llm_analysis: dict[str, Any] | None = None
    rules_matched: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    success: bool = True
    dry_run: bool = False


class Rule(BaseModel):
    """Automation rule definition."""

    id: str
    name: str
    description: str = ""
    enabled: bool = True
    priority: int = 0  # Higher = runs first

    # Conditions (all must match)
    conditions: list["RuleCondition"] = Field(default_factory=list)

    # Actions to take when conditions match
    actions: list["RuleAction"] = Field(default_factory=list)


class RuleCondition(BaseModel):
    """A condition that must be met for a rule to trigger."""

    field: str  # e.g., "from_addr", "subject", "category"
    operator: str  # e.g., "contains", "equals", "matches", "in"
    value: Any


class RuleAction(BaseModel):
    """An action to take when a rule matches."""

    type: str  # e.g., "move", "label", "forward", "archive", "llm_process"
    params: dict[str, Any] = Field(default_factory=dict)


class ActionType(str, Enum):
    """Types of actions that can be audited."""

    MOVE = "move"
    DELETE = "delete"
    FLAG = "flag"
    UNFLAG = "unflag"
    ARCHIVE = "archive"
    DRAFT_CREATED = "draft_created"
    DRAFT_APPROVED = "draft_approved"
    DRAFT_DISCARDED = "draft_discarded"
    SENT = "sent"


class AuditEntry(BaseModel):
    """Audit log entry for tracking email operations."""

    id: str  # UUID
    timestamp: datetime = Field(default_factory=datetime.now)
    action_type: ActionType
    email_id: str
    email_subject: str  # For human readability
    rule_name: str | None = None  # Which rule triggered this, if any
    source_folder: str | None = None
    target_folder: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False  # Was this a preview or real action?


class DraftStatus(str, Enum):
    """Status of a draft reply."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    DISCARDED = "discarded"


class DraftReply(BaseModel):
    """A draft reply created by LLM processing."""

    id: str  # UUID
    original_email_id: str
    original_subject: str
    recipient: str
    draft_body: str
    created_at: datetime = Field(default_factory=datetime.now)
    status: DraftStatus = DraftStatus.PENDING_REVIEW
    instructions: str | None = None  # Instructions given to LLM for drafting


class ActionItemStatus(str, Enum):
    """Status of an action item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class DigestStatus(str, Enum):
    """Delivery status of a digest."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class ProcessedEmail(BaseModel):
    """Record of a processed email in the service."""

    id: str  # SHA256(message_id or source:folder:email_id)
    message_id: str | None = None
    email_id: str
    source: str
    folder: str
    processed_at: datetime = Field(default_factory=datetime.now)
    digest_id: str | None = None
    classification: dict[str, Any] | None = None  # {category, priority}
    llm_analysis: dict[str, Any] | None = None  # Full analysis blob

    # Email metadata for display
    subject: str | None = None
    from_addr: str | None = None
    date: datetime | None = None


class Digest(BaseModel):
    """Email digest summary."""

    id: str  # UUID
    created_at: datetime = Field(default_factory=datetime.now)
    period_start: datetime
    period_end: datetime
    email_count: int
    summary: str
    raw_content: str | None = None  # Full markdown
    delivery_status: DigestStatus = DigestStatus.PENDING


class ActionItem(BaseModel):
    """An action item extracted from an email."""

    id: str  # UUID
    email_id: str  # FK to processed_emails
    digest_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    title: str
    description: str | None = None
    priority: EmailPriority = EmailPriority.NORMAL
    urgency: str = "normal"  # low/normal/high/urgent
    due_date: datetime | None = None
    status: ActionItemStatus = ActionItemStatus.PENDING
    completed_at: datetime | None = None
    relevance: str = "direct"  # "direct" or "informational"
    metadata: dict[str, Any] = Field(default_factory=dict)  # JSON for extensibility
