"""Command-line interface for emma."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from email_agent import __version__
from email_agent.audit import AuditLogger
from email_agent.config import IMAPConfig, MaildirConfig, Settings, load_settings
from email_agent.models import ActionType, DraftReply, DraftStatus, Email
from email_agent.processors.llm import LLMProcessor
from email_agent.sources.imap import IMAPSource
from email_agent.sources.maildir import MaildirSource

app = typer.Typer(
    name="emma",
    help="Email automation platform with LLM processing and rules engine.",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"emma version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-v", callback=version_callback, is_eager=True),
    ] = False,
) -> None:
    """Email automation platform."""
    pass


# ─── Source Commands ────────────────────────────────────────────────────────


source_app = typer.Typer(help="Manage email sources")
app.add_typer(source_app, name="source")


@source_app.command("list")
def source_list() -> None:
    """List configured email sources."""
    settings = load_settings()

    table = Table(title="Configured Email Sources")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Details")

    for name, cfg in settings.imap_accounts.items():
        table.add_row(name, "IMAP", f"{cfg.host}:{cfg.port}")

    for name, cfg in settings.maildir_accounts.items():
        table.add_row(name, "Maildir", str(cfg.path))

    if settings.mxroute.enabled:
        table.add_row("mxroute", "MCP", settings.mxroute.domain or "all domains")

    if table.row_count == 0:
        console.print("[yellow]No email sources configured.[/yellow]")
        console.print("Configure sources in ~/.config/emma/config.yaml")
    else:
        console.print(table)


@source_app.command("test")
def source_test(
    ctx: typer.Context,
    source_type: Annotated[str, typer.Argument(help="Source type: imap, maildir")],
    host: Annotated[str | None, typer.Option(help="IMAP host")] = None,
    port: Annotated[int, typer.Option(help="IMAP port")] = 993,
    username: Annotated[str | None, typer.Option(help="IMAP username")] = None,
    password: Annotated[str | None, typer.Option(help="IMAP password")] = None,
    path: Annotated[str | None, typer.Option(help="Maildir path")] = None,
) -> None:
    """Test connection to an email source."""
    if source_type == "imap":
        if not all([host, username, password]):
            _error_with_help(ctx, "IMAP requires --host, --username, and --password")
    elif source_type == "maildir":
        if not path:
            _error_with_help(ctx, "Maildir requires --path")
    elif source_type not in ("imap", "maildir"):
        _error_with_help(ctx, f"Unknown source type: {source_type}. Must be 'imap' or 'maildir'")

    async def _test() -> None:
        if source_type == "imap":
            config = IMAPConfig(host=host, port=port, username=username, password=password)  # type: ignore
            source = IMAPSource(config, name="test")
        else:  # maildir
            config = MaildirConfig(path=Path(path))  # type: ignore
            source = MaildirSource(config, name="test")

        try:
            console.print(f"Connecting to {source_type}...")
            await source.connect()
            folders = await source.list_folders()
            console.print(f"[green]Connected![/green]")
            console.print(f"Available folders: {', '.join(folders)}")
            await source.disconnect()
        except Exception as e:
            console.print(f"[red]Connection failed: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_test())


# ─── Email Commands ─────────────────────────────────────────────────────────


email_app = typer.Typer(help="Email operations")
app.add_typer(email_app, name="email")


@email_app.command("list")
def email_list(
    ctx: typer.Context,
    source: Annotated[str, typer.Option(help="Source name")] = "default",
    folder: Annotated[str, typer.Option(help="Folder to list")] = "INBOX",
    limit: Annotated[int, typer.Option(help="Max emails to show")] = 20,
) -> None:
    """List emails from a source."""
    settings = load_settings()
    email_source = _get_source(settings, source)
    if not email_source:
        _error_with_help(ctx, f"Source '{source}' not found")

    async def _list() -> None:

        async with email_source:
            table = Table(title=f"Emails in {folder}")
            table.add_column("ID", style="dim", width=8)
            table.add_column("Date", width=12)
            table.add_column("From", width=25)
            table.add_column("Subject")

            count = 0
            async for email in email_source.fetch_emails(folder=folder, limit=limit):
                date_str = email.date.strftime("%Y-%m-%d") if email.date else "?"
                from_addr = email.from_addr[:25] if len(email.from_addr) > 25 else email.from_addr
                subject = email.subject[:50] if len(email.subject) > 50 else email.subject
                table.add_row(email.id[:8], date_str, from_addr, subject)
                count += 1

            console.print(table)
            console.print(f"Showing {count} emails")

    asyncio.run(_list())


@email_app.command("show")
def email_show(
    ctx: typer.Context,
    email_id: Annotated[str, typer.Argument(help="Email ID")],
    source: Annotated[str, typer.Option(help="Source name")] = "default",
    folder: Annotated[str, typer.Option(help="Folder")] = "INBOX",
) -> None:
    """Show details of a specific email."""
    settings = load_settings()
    email_source = _get_source(settings, source)
    if not email_source:
        _error_with_help(ctx, f"Source '{source}' not found")

    async def _show() -> None:
        async with email_source:
            email = await email_source.get_email(email_id, folder)
            if not email:
                console.print(f"[red]Email not found: {email_id}[/red]")
                raise typer.Exit(1)

            _display_email(email)

    asyncio.run(_show())


@email_app.command("delete")
def email_delete(
    ctx: typer.Context,
    email_id: Annotated[str, typer.Argument(help="Email ID to delete")],
    source: Annotated[str, typer.Option(help="Source name")] = "default",
    folder: Annotated[str, typer.Option(help="Folder")] = "INBOX",
    permanent: Annotated[bool, typer.Option("--permanent", help="Permanently delete (skip Trash)")] = False,
    execute: Annotated[bool, typer.Option("--execute", help="Actually perform the delete")] = False,
) -> None:
    """Delete an email (moves to Trash by default).

    Without --execute, shows what would happen (dry-run).
    Use --permanent to skip Trash and permanently delete.
    """
    settings = load_settings()
    email_source = _get_source(settings, source)
    if not email_source:
        _error_with_help(ctx, f"Source '{source}' not found")

    async def _delete() -> None:
        async with email_source:
            email = await email_source.get_email(email_id, folder)
            if not email:
                console.print(f"[red]Email not found: {email_id}[/red]")
                raise typer.Exit(1)

            if permanent:
                action_desc = "permanently delete"
            else:
                action_desc = f"move to {email_source.trash_folder}"

            if not execute:
                # Dry run
                console.print("[yellow][DRY RUN][/yellow] Would delete email:")
                console.print(f"  Subject: {email.subject}")
                console.print(f"  From: {email.from_addr}")
                console.print(f"  Action: {action_desc}")
                console.print(f"\nTo execute, run: emma email delete {email_id} --execute")
                if not permanent:
                    console.print(f"To permanently delete: emma email delete {email_id} --permanent --execute")
                return

            # Execute delete
            success = await email_source.delete_email(email_id, folder, permanent=permanent)

            if success:
                console.print(f"[green]Email deleted ({action_desc})[/green]")

                # Log to audit
                if settings.guardrails.audit_enabled:
                    logger = _get_audit_logger(settings)
                    logger.log_action(
                        ActionType.DELETE,
                        email_id=email.id,
                        email_subject=email.subject,
                        source_folder=folder,
                        target_folder=None if permanent else email_source.trash_folder,
                        details={"permanent": permanent},
                    )
            else:
                console.print(f"[red]Failed to delete email[/red]")
                raise typer.Exit(1)

    asyncio.run(_delete())


@email_app.command("move")
def email_move(
    ctx: typer.Context,
    email_id: Annotated[str, typer.Argument(help="Email ID to move")],
    to_folder: Annotated[str, typer.Argument(help="Destination folder")],
    source: Annotated[str, typer.Option(help="Source name")] = "default",
    from_folder: Annotated[str, typer.Option(help="Source folder")] = "INBOX",
    execute: Annotated[bool, typer.Option("--execute", help="Actually perform the move")] = False,
) -> None:
    """Move an email to another folder.

    Without --execute, shows what would happen (dry-run).
    """
    settings = load_settings()
    email_source = _get_source(settings, source)
    if not email_source:
        _error_with_help(ctx, f"Source '{source}' not found")

    async def _move() -> None:
        async with email_source:
            email = await email_source.get_email(email_id, from_folder)
            if not email:
                console.print(f"[red]Email not found: {email_id}[/red]")
                raise typer.Exit(1)

            if not execute:
                # Dry run
                console.print("[yellow][DRY RUN][/yellow] Would move email:")
                console.print(f"  Subject: {email.subject}")
                console.print(f"  From folder: {from_folder}")
                console.print(f"  To folder: {to_folder}")
                console.print(f"\nTo execute, run: emma email move {email_id} {to_folder} --execute")
                return

            # Execute move
            success = await email_source.move_email(email_id, from_folder, to_folder)

            if success:
                console.print(f"[green]Email moved to {to_folder}[/green]")

                # Log to audit
                if settings.guardrails.audit_enabled:
                    logger = _get_audit_logger(settings)
                    logger.log_action(
                        ActionType.MOVE,
                        email_id=email.id,
                        email_subject=email.subject,
                        source_folder=from_folder,
                        target_folder=to_folder,
                    )
            else:
                console.print(f"[red]Failed to move email[/red]")
                raise typer.Exit(1)

    asyncio.run(_move())


def _display_email(email: Email) -> None:
    """Display email details."""
    console.print(f"\n[bold cyan]Subject:[/bold cyan] {email.subject}")
    console.print(f"[bold]From:[/bold] {email.from_addr}")
    console.print(f"[bold]To:[/bold] {', '.join(email.to_addrs)}")
    if email.cc_addrs:
        console.print(f"[bold]CC:[/bold] {', '.join(email.cc_addrs)}")
    console.print(f"[bold]Date:[/bold] {email.date}")
    console.print(f"[bold]Folder:[/bold] {email.folder}")
    if email.attachments:
        console.print(f"[bold]Attachments:[/bold] {len(email.attachments)}")
        for att in email.attachments:
            console.print(f"  - {att.filename} ({att.content_type})")

    console.print("\n[bold]Body:[/bold]")
    console.print("─" * 60)
    console.print(email.body_text[:2000])
    if len(email.body_text) > 2000:
        console.print(f"\n[dim]... truncated ({len(email.body_text)} chars total)[/dim]")


# ─── Analyze Commands ───────────────────────────────────────────────────────


analyze_app = typer.Typer(help="LLM-powered email analysis")
app.add_typer(analyze_app, name="analyze")


def _check_llm_config(settings: Settings, ctx: typer.Context) -> None:
    """Verify LLM is configured correctly."""
    if settings.llm.provider == "anthropic" and not settings.anthropic_api_key:
        _error_with_help(ctx, "ANTHROPIC_API_KEY not set (required for anthropic provider)")


def _create_processor(settings: Settings) -> LLMProcessor:
    """Create LLM processor with appropriate config."""
    return LLMProcessor(settings.llm, settings.anthropic_api_key)


@analyze_app.command("email")
def analyze_email(
    ctx: typer.Context,
    email_id: Annotated[str, typer.Argument(help="Email ID to analyze")],
    source: Annotated[str, typer.Option(help="Source name")] = "default",
    folder: Annotated[str, typer.Option(help="Folder")] = "INBOX",
) -> None:
    """Analyze an email using LLM."""
    settings = load_settings()
    _check_llm_config(settings, ctx)
    email_source = _get_source(settings, source)
    if not email_source:
        _error_with_help(ctx, f"Source '{source}' not found")

    async def _analyze() -> None:
        async with email_source:
            email = await email_source.get_email(email_id, folder)
            if not email:
                console.print(f"[red]Email not found: {email_id}[/red]")
                raise typer.Exit(1)

            console.print(f"Analyzing email: {email.subject[:50]}...")
            processor = _create_processor(settings)
            analysis = await processor.analyze_email(email)

            console.print("\n[bold cyan]Analysis Results:[/bold cyan]")
            for key, value in analysis.items():
                console.print(f"[bold]{key}:[/bold] {value}")

    asyncio.run(_analyze())


@analyze_app.command("summarize")
def analyze_summarize(
    ctx: typer.Context,
    email_id: Annotated[str, typer.Argument(help="Email ID")],
    source: Annotated[str, typer.Option(help="Source name")] = "default",
    folder: Annotated[str, typer.Option(help="Folder")] = "INBOX",
) -> None:
    """Generate a summary of an email."""
    settings = load_settings()
    _check_llm_config(settings, ctx)
    email_source = _get_source(settings, source)
    if not email_source:
        _error_with_help(ctx, f"Source '{source}' not found")

    async def _summarize() -> None:
        async with email_source:
            email = await email_source.get_email(email_id, folder)
            if not email:
                console.print(f"[red]Email not found: {email_id}[/red]")
                raise typer.Exit(1)

            console.print(f"Summarizing: {email.subject[:50]}...")
            processor = _create_processor(settings)
            summary = await processor.summarize_email(email)

            console.print(f"\n[bold cyan]Summary:[/bold cyan] {summary}")

    asyncio.run(_summarize())


@analyze_app.command("draft-reply")
def analyze_draft_reply(
    ctx: typer.Context,
    email_id: Annotated[str, typer.Argument(help="Email ID")],
    instructions: Annotated[str, typer.Option(help="Reply instructions")] = "",
    source: Annotated[str, typer.Option(help="Source name")] = "default",
    folder: Annotated[str, typer.Option(help="Folder")] = "INBOX",
) -> None:
    """Draft a reply to an email.

    Creates a draft that must be reviewed and approved before sending.
    Use 'emma draft list' to see pending drafts.
    """
    settings = load_settings()
    _check_llm_config(settings, ctx)
    email_source = _get_source(settings, source)
    if not email_source:
        _error_with_help(ctx, f"Source '{source}' not found")

    async def _draft() -> None:
        async with email_source:
            email = await email_source.get_email(email_id, folder)
            if not email:
                console.print(f"[red]Email not found: {email_id}[/red]")
                raise typer.Exit(1)

            console.print(f"Drafting reply to: {email.subject[:50]}...")
            processor = _create_processor(settings)
            draft = await processor.draft_reply(email, instructions)

            # Save draft to storage
            drafts = _load_drafts(settings)
            drafts[draft.id] = draft
            _save_drafts(settings, drafts)

            # Log to audit
            if settings.guardrails.audit_enabled:
                logger = _get_audit_logger(settings)
                logger.log_action(
                    ActionType.DRAFT_CREATED,
                    email_id=email.id,
                    email_subject=email.subject,
                    details={
                        "draft_id": draft.id,
                        "recipient": draft.recipient,
                        "instructions": instructions or None,
                    },
                )

            console.print("\n[bold cyan]Draft Reply Created:[/bold cyan]")
            console.print(f"[bold]Draft ID:[/bold] {draft.id[:8]}")
            console.print(f"[bold]Status:[/bold] {draft.status.value}")
            console.print("─" * 60)
            console.print(draft.draft_body)
            console.print("─" * 60)
            console.print("\n[yellow]This draft requires review before sending.[/yellow]")
            console.print(f"  View:    emma draft show {draft.id[:8]}")
            console.print(f"  Approve: emma draft approve {draft.id[:8]}")
            console.print(f"  Discard: emma draft discard {draft.id[:8]}")

    asyncio.run(_draft())


# ─── Config Commands ────────────────────────────────────────────────────────


config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    settings = load_settings()

    console.print("[bold cyan]Emma Configuration[/bold cyan]")
    console.print(f"Config dir: {settings.config_dir}")
    console.print(f"Data dir: {settings.data_dir}")
    console.print(f"Database: {settings.db_path}")

    console.print(f"\n[bold]LLM Settings:[/bold]")
    console.print(f"  Provider: {settings.llm.provider}")
    console.print(f"  Model: {settings.llm.model}")
    if settings.llm.provider == "ollama":
        console.print(f"  Ollama URL: {settings.llm.ollama_base_url}")
        console.print(f"  Context length: {settings.llm.ollama_context_length}")
    elif settings.llm.provider == "anthropic":
        console.print(f"  API Key: {'set' if settings.anthropic_api_key else 'not set'}")

    console.print(f"\n[bold]IMAP Accounts:[/bold] {len(settings.imap_accounts)}")
    console.print(f"[bold]Maildir Accounts:[/bold] {len(settings.maildir_accounts)}")
    console.print(f"[bold]MXroute:[/bold] {'enabled' if settings.mxroute.enabled else 'disabled'}")


@config_app.command("init")
def config_init() -> None:
    """Initialize configuration directory."""
    settings = load_settings()
    settings.ensure_dirs()

    config_file = settings.config_dir / "config.yaml"
    if not config_file.exists():
        config_file.write_text(
            """# Emma Configuration
# See documentation for full options

# IMAP accounts
# imap_accounts:
#   personal:
#     host: imap.example.com
#     port: 993
#     username: user@example.com
#     password: ${IMAP_PASSWORD}  # Use env var

# Local Maildir
# maildir_accounts:
#   thunderbird:
#     path: ~/.thunderbird/profile/ImapMail/imap.example.com
#     account_name: personal

# MXroute MCP integration
# mxroute:
#   enabled: true
#   domain: example.com

# LLM settings (default: local Ollama)
llm:
  provider: ollama
  model: gpt-oss:20b
  max_tokens: 1024
  ollama_base_url: http://localhost:11434
  ollama_context_length: 24576  # 24k context

# For Anthropic API instead:
# llm:
#   provider: anthropic
#   model: claude-sonnet-4-20250514
#   max_tokens: 1024
# Also set: ANTHROPIC_API_KEY env var
"""
        )
        console.print(f"[green]Created config file: {config_file}[/green]")
    else:
        console.print(f"Config file already exists: {config_file}")

    console.print(f"[green]Configuration initialized at {settings.config_dir}[/green]")


# ─── Helper Functions ───────────────────────────────────────────────────────


def _error_with_help(ctx: typer.Context, message: str) -> None:
    """Print error message followed by relevant help text, then exit."""
    console.print(f"[red]Error: {message}[/red]\n")
    console.print(ctx.get_help())
    raise typer.Exit(1)


def _get_source(
    settings: Settings, name: str, trash_folder: str | None = None
) -> IMAPSource | MaildirSource | None:
    """Get an email source by name."""
    trash = trash_folder or settings.guardrails.trash_folder
    if name in settings.imap_accounts:
        return IMAPSource(settings.imap_accounts[name], name=name, trash_folder=trash)
    if name in settings.maildir_accounts:
        return MaildirSource(settings.maildir_accounts[name], name=name, trash_folder=trash)
    return None


def _get_audit_logger(settings: Settings) -> AuditLogger:
    """Get the audit logger instance."""
    settings.ensure_dirs()
    audit_db = settings.data_dir / settings.guardrails.audit_db_name
    return AuditLogger(audit_db)


def _get_drafts_file(settings: Settings) -> Path:
    """Get the path to the drafts JSON file."""
    settings.ensure_dirs()
    return settings.data_dir / "drafts.json"


def _load_drafts(settings: Settings) -> dict[str, DraftReply]:
    """Load drafts from storage."""
    drafts_file = _get_drafts_file(settings)
    if not drafts_file.exists():
        return {}
    try:
        data = json.loads(drafts_file.read_text())
        return {k: DraftReply.model_validate(v) for k, v in data.items()}
    except Exception:
        return {}


def _save_drafts(settings: Settings, drafts: dict[str, DraftReply]) -> None:
    """Save drafts to storage."""
    drafts_file = _get_drafts_file(settings)
    data = {k: v.model_dump(mode="json") for k, v in drafts.items()}
    drafts_file.write_text(json.dumps(data, indent=2, default=str))


# ─── Audit Commands ─────────────────────────────────────────────────────────


audit_app = typer.Typer(help="View audit log of email operations")
app.add_typer(audit_app, name="audit")


@audit_app.command("list")
def audit_list(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(help="Max entries to show")] = 20,
    action: Annotated[str | None, typer.Option(help="Filter by action type")] = None,
    email_id: Annotated[str | None, typer.Option(help="Filter by email ID")] = None,
    include_dry_run: Annotated[bool, typer.Option(help="Include dry-run entries")] = False,
) -> None:
    """List recent audit log entries."""
    settings = load_settings()
    logger = _get_audit_logger(settings)

    action_type = None
    if action:
        try:
            action_type = ActionType(action)
        except ValueError:
            valid_types = ", ".join(a.value for a in ActionType)
            _error_with_help(ctx, f"Unknown action type: {action}. Valid types: {valid_types}")

    entries = logger.get_history(
        email_id=email_id,
        action_type=action_type,
        limit=limit,
        include_dry_run=include_dry_run,
    )

    if not entries:
        console.print("[yellow]No audit entries found.[/yellow]")
        return

    table = Table(title="Audit Log")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Timestamp", width=19)
    table.add_column("Action", style="cyan", width=12)
    table.add_column("Subject", width=30)
    table.add_column("Rule", width=15)
    table.add_column("Dry Run", width=8)

    for entry in entries:
        subject = entry.email_subject[:28] + "..." if len(entry.email_subject) > 30 else entry.email_subject
        dry_run = "[yellow]Yes[/yellow]" if entry.dry_run else "[green]No[/green]"
        table.add_row(
            entry.id[:8],
            entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            entry.action_type.value,
            subject,
            entry.rule_name or "-",
            dry_run,
        )

    console.print(table)


@audit_app.command("show")
def audit_show(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Audit entry ID (or prefix)")],
) -> None:
    """Show details of an audit entry."""
    settings = load_settings()
    logger = _get_audit_logger(settings)

    # Try to find entry by full ID or prefix
    entry = logger.get_entry(entry_id)
    if not entry:
        # Try prefix match
        for e in logger.iter_all(include_dry_run=True):
            if e.id.startswith(entry_id):
                entry = e
                break

    if not entry:
        _error_with_help(ctx, f"Audit entry not found: {entry_id}")

    console.print(Panel(f"[bold]Audit Entry: {entry.id}[/bold]"))
    console.print(f"[bold]Timestamp:[/bold] {entry.timestamp}")
    console.print(f"[bold]Action:[/bold] {entry.action_type.value}")
    console.print(f"[bold]Email ID:[/bold] {entry.email_id}")
    console.print(f"[bold]Subject:[/bold] {entry.email_subject}")
    console.print(f"[bold]Rule:[/bold] {entry.rule_name or 'N/A'}")
    console.print(f"[bold]Dry Run:[/bold] {'Yes' if entry.dry_run else 'No'}")

    if entry.source_folder or entry.target_folder:
        console.print(f"[bold]Source Folder:[/bold] {entry.source_folder or 'N/A'}")
        console.print(f"[bold]Target Folder:[/bold] {entry.target_folder or 'N/A'}")

    if entry.details:
        console.print(f"\n[bold]Details:[/bold]")
        console.print(json.dumps(entry.details, indent=2))


@audit_app.command("export")
def audit_export(
    ctx: typer.Context,
    format: Annotated[str, typer.Option(help="Output format: json or csv")] = "json",
    output: Annotated[str | None, typer.Option(help="Output file (default: stdout)")] = None,
    include_dry_run: Annotated[bool, typer.Option(help="Include dry-run entries")] = False,
) -> None:
    """Export audit log to file."""
    settings = load_settings()
    logger = _get_audit_logger(settings)

    if format not in ("json", "csv"):
        _error_with_help(ctx, f"Unknown format: {format}. Use 'json' or 'csv'")

    exported = logger.export_log(format=format, include_dry_run=include_dry_run)  # type: ignore

    if output:
        Path(output).write_text(exported)
        console.print(f"[green]Exported to {output}[/green]")
    else:
        console.print(exported)


# ─── Draft Commands ─────────────────────────────────────────────────────────


draft_app = typer.Typer(help="Manage draft replies")
app.add_typer(draft_app, name="draft")


@draft_app.command("list")
def draft_list(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
) -> None:
    """List pending draft replies."""
    settings = load_settings()
    drafts = _load_drafts(settings)

    if status:
        try:
            filter_status = DraftStatus(status)
            drafts = {k: v for k, v in drafts.items() if v.status == filter_status}
        except ValueError:
            valid_statuses = ", ".join(s.value for s in DraftStatus)
            _error_with_help(ctx, f"Unknown status: {status}. Valid statuses: {valid_statuses}")

    if not drafts:
        console.print("[yellow]No drafts found.[/yellow]")
        return

    table = Table(title="Draft Replies")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Created", width=19)
    table.add_column("Status", width=15)
    table.add_column("To", width=25)
    table.add_column("Re: Subject", width=30)

    for draft_id, draft in drafts.items():
        subject = draft.original_subject[:28] + "..." if len(draft.original_subject) > 30 else draft.original_subject
        recipient = draft.recipient[:23] + "..." if len(draft.recipient) > 25 else draft.recipient

        status_str = draft.status.value
        if draft.status == DraftStatus.PENDING_REVIEW:
            status_str = f"[yellow]{status_str}[/yellow]"
        elif draft.status == DraftStatus.APPROVED:
            status_str = f"[green]{status_str}[/green]"
        else:
            status_str = f"[red]{status_str}[/red]"

        table.add_row(
            draft_id[:8],
            draft.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            status_str,
            recipient,
            f"Re: {subject}",
        )

    console.print(table)


@draft_app.command("show")
def draft_show(
    ctx: typer.Context,
    draft_id: Annotated[str, typer.Argument(help="Draft ID (or prefix)")],
) -> None:
    """Show contents of a draft reply."""
    settings = load_settings()
    drafts = _load_drafts(settings)

    # Find draft by ID or prefix
    draft = None
    full_id = None
    for did, d in drafts.items():
        if did == draft_id or did.startswith(draft_id):
            draft = d
            full_id = did
            break

    if not draft or not full_id:
        _error_with_help(ctx, f"Draft not found: {draft_id}")

    console.print(Panel(f"[bold]Draft Reply: {full_id}[/bold]"))
    console.print(f"[bold]Status:[/bold] {draft.status.value}")
    console.print(f"[bold]Created:[/bold] {draft.created_at}")
    console.print(f"[bold]To:[/bold] {draft.recipient}")
    console.print(f"[bold]Re:[/bold] {draft.original_subject}")
    if draft.instructions:
        console.print(f"[bold]Instructions:[/bold] {draft.instructions}")

    console.print("\n[bold cyan]Draft Body:[/bold cyan]")
    console.print("─" * 60)
    console.print(draft.draft_body)


@draft_app.command("approve")
def draft_approve(
    ctx: typer.Context,
    draft_id: Annotated[str, typer.Argument(help="Draft ID to approve")],
) -> None:
    """Approve a draft (marks it ready for sending)."""
    settings = load_settings()
    drafts = _load_drafts(settings)

    # Find draft
    full_id = None
    for did in drafts:
        if did == draft_id or did.startswith(draft_id):
            full_id = did
            break

    if not full_id:
        _error_with_help(ctx, f"Draft not found: {draft_id}")

    draft = drafts[full_id]
    if draft.status != DraftStatus.PENDING_REVIEW:
        console.print(f"[yellow]Draft is already {draft.status.value}[/yellow]")
        return

    draft.status = DraftStatus.APPROVED
    _save_drafts(settings, drafts)

    # Log to audit
    if settings.guardrails.audit_enabled:
        logger = _get_audit_logger(settings)
        logger.log_action(
            ActionType.DRAFT_APPROVED,
            email_id=draft.original_email_id,
            email_subject=draft.original_subject,
            details={"draft_id": full_id, "recipient": draft.recipient},
        )

    console.print(f"[green]Draft approved: {full_id[:8]}[/green]")
    console.print("[yellow]Note: Manual send required. EMMA does not auto-send emails.[/yellow]")


@draft_app.command("discard")
def draft_discard(
    ctx: typer.Context,
    draft_id: Annotated[str, typer.Argument(help="Draft ID to discard")],
) -> None:
    """Discard a draft reply."""
    settings = load_settings()
    drafts = _load_drafts(settings)

    # Find draft
    full_id = None
    for did in drafts:
        if did == draft_id or did.startswith(draft_id):
            full_id = did
            break

    if not full_id:
        _error_with_help(ctx, f"Draft not found: {draft_id}")

    draft = drafts[full_id]

    # Log to audit before removing
    if settings.guardrails.audit_enabled:
        logger = _get_audit_logger(settings)
        logger.log_action(
            ActionType.DRAFT_DISCARDED,
            email_id=draft.original_email_id,
            email_subject=draft.original_subject,
            details={"draft_id": full_id, "recipient": draft.recipient},
        )

    del drafts[full_id]
    _save_drafts(settings, drafts)

    console.print(f"[green]Draft discarded: {full_id[:8]}[/green]")


if __name__ == "__main__":
    app()
