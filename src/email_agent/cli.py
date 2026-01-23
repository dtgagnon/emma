"""Command-line interface for emma."""

import asyncio
import json
import os
import shutil
import subprocess
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
from email_agent.tui import select_email
from email_agent.models import ActionItemStatus, EmailPriority

app = typer.Typer(
    name="emma",
    help="Email automation platform with LLM processing and rules engine.",
    no_args_is_help=True,
    add_completion=False,  # Use custom completion command instead
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


source_app = typer.Typer(help="Manage email sources", no_args_is_help=True)
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


email_app = typer.Typer(help="Email operations", no_args_is_help=True)
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
    source: Annotated[str | None, typer.Argument(help="Source name (optional)")] = None,
    folder: Annotated[str | None, typer.Argument(help="Folder (optional)")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max emails to fetch")] = 100,
) -> None:
    """Interactively browse and view emails.

    Opens an fzf-style selector to fuzzy search and select emails.
    Falls back to numbered list if fzf is not installed.

    Examples:
        emma email show                    # All sources, all folders
        emma email show default            # Specific source, all folders
        emma email show default INBOX      # Specific source and folder
    """
    settings = load_settings()

    async def _show() -> None:
        emails: list[Email] = []

        # Determine which sources to use
        if source:
            # Single source specified
            email_source = _get_source(settings, source)
            if not email_source:
                console.print(f"[red]Source '{source}' not found[/red]")
                raise typer.Exit(1)

            async with email_source:
                if folder:
                    # Specific folder
                    async for email in email_source.fetch_emails(folder=folder, limit=limit):
                        emails.append(email)
                else:
                    # All folders in this source
                    folders = await email_source.list_folders()
                    per_folder_limit = max(1, limit // len(folders)) if folders else limit
                    for f in folders:
                        async for email in email_source.fetch_emails(folder=f, limit=per_folder_limit):
                            emails.append(email)
        else:
            # All configured sources
            source_names = list(settings.imap_accounts.keys()) + list(settings.maildir_accounts.keys())
            if not source_names:
                console.print("[yellow]No email sources configured.[/yellow]")
                console.print("Configure sources in ~/.config/emma/config.yaml")
                raise typer.Exit(1)

            per_source_limit = max(1, limit // len(source_names))
            for src_name in source_names:
                email_source = _get_source(settings, src_name)
                if email_source:
                    async with email_source:
                        folders = await email_source.list_folders()
                        per_folder_limit = max(1, per_source_limit // len(folders)) if folders else per_source_limit
                        for f in folders:
                            async for email in email_source.fetch_emails(folder=f, limit=per_folder_limit):
                                emails.append(email)

        if not emails:
            console.print("[yellow]No emails found.[/yellow]")
            raise typer.Exit(0)

        # Sort by date, newest first
        emails.sort(key=lambda e: e.date or datetime.min, reverse=True)

        # Trim to limit
        emails = emails[:limit]

        # Interactive selection
        selected = select_email(emails)
        if selected:
            _display_email(selected)

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


analyze_app = typer.Typer(help="LLM-powered email analysis", no_args_is_help=True)
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


config_app = typer.Typer(help="Configuration management", no_args_is_help=True)
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


# ─── Completion Commands ─────────────────────────────────────────────────────


completion_app = typer.Typer(help="Shell completion management", no_args_is_help=True)
app.add_typer(completion_app, name="completion")

# Shells supported natively by typer
TYPER_SUPPORTED_SHELLS = {"bash", "zsh", "fish", "powershell", "pwsh"}


def _detect_shell() -> str:
    """Detect the current shell."""
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name
    # Fallback for Windows
    if os.name == "nt":
        return "powershell"
    return "unknown"


def _get_carapace_spec_path() -> Path | None:
    """Get the path to the bundled carapace spec file."""
    # Check relative to this module (for installed package)
    module_dir = Path(__file__).parent
    spec_locations = [
        module_dir / "completions" / "emma.yaml",
        module_dir.parent.parent / "completions" / "emma.yaml",  # dev layout
    ]
    for path in spec_locations:
        if path.exists():
            return path
    return None


def _install_carapace_completion(shell: str) -> bool:
    """Install completion using carapace."""
    carapace_bin = shutil.which("carapace")
    if not carapace_bin:
        return False

    spec_path = _get_carapace_spec_path()
    if not spec_path:
        console.print("[yellow]Carapace spec file not found in package.[/yellow]")
        console.print("You can manually create it at ~/.config/carapace/specs/emma.yaml")
        return False

    # Copy spec to carapace's spec directory
    carapace_spec_dir = Path.home() / ".config" / "carapace" / "specs"
    carapace_spec_dir.mkdir(parents=True, exist_ok=True)
    target_spec = carapace_spec_dir / "emma.yaml"

    shutil.copy(spec_path, target_spec)
    console.print(f"[green]Installed carapace spec to {target_spec}[/green]")

    # Show shell-specific activation instructions
    console.print(f"\n[bold]To activate completions for {shell}:[/bold]")
    if shell in ("nu", "nushell"):
        console.print("Add to your config.nu:")
        console.print('  source ~/.cache/carapace/init.nu')
        console.print("\nOr run: carapace _carapace nushell | save -f ~/.cache/carapace/init.nu")
    elif shell == "elvish":
        console.print("Add to your rc.elv:")
        console.print('  eval (carapace _carapace elvish | slurp)')
    elif shell == "xonsh":
        console.print("Add to your .xonshrc:")
        console.print('  exec($(carapace _carapace xonsh))')
    elif shell == "tcsh":
        console.print("Add to your .tcshrc:")
        console.print('  eval `carapace _carapace tcsh`')
    else:
        console.print(f"Run: carapace _carapace {shell}")

    return True


@completion_app.command("install")
def completion_install(
    shell: Annotated[str | None, typer.Option(help="Shell to install completion for")] = None,
) -> None:
    """Install shell completions.

    Automatically detects your shell. Uses typer's built-in completion for
    bash/zsh/fish/powershell, falls back to carapace for other shells (nu, elvish, etc).
    """
    detected_shell = shell or _detect_shell()

    if detected_shell == "unknown":
        console.print("[red]Could not detect shell.[/red]")
        console.print("Specify explicitly with: emma completion install --shell <shell>")
        raise typer.Exit(1)

    console.print(f"Detected shell: [cyan]{detected_shell}[/cyan]")

    # Try typer's built-in completion for supported shells
    if detected_shell in TYPER_SUPPORTED_SHELLS:
        try:
            # Use typer's completion installation
            import click.shell_completion

            shell_map = {
                "bash": "bash",
                "zsh": "zsh",
                "fish": "fish",
                "powershell": "powershell",
                "pwsh": "powershell",
            }
            shell_name = shell_map.get(detected_shell, detected_shell)

            # Get completion script
            from typer import main as typer_main

            shell_complete = click.shell_completion.get_completion_class(shell_name)
            if shell_complete:
                comp = shell_complete(app, {}, "emma", "_EMMA_COMPLETE")
                script = comp.source()
                console.print(f"\n[bold]Add this to your shell config:[/bold]\n")
                console.print(script)
                return
        except Exception as e:
            console.print(f"[yellow]Typer completion failed: {e}[/yellow]")

    # Fall back to carapace for unsupported shells
    console.print(f"[yellow]Shell '{detected_shell}' not supported by typer.[/yellow]")
    console.print("Checking for carapace...")

    if shutil.which("carapace"):
        if _install_carapace_completion(detected_shell):
            return
    else:
        console.print("[red]carapace not found in PATH.[/red]")
        console.print("\nTo get completions for this shell, install carapace:")
        console.print("  https://carapace.sh")
        console.print("\nOr use a supported shell: bash, zsh, fish, powershell")

    raise typer.Exit(1)


@completion_app.command("show")
def completion_show(
    shell: Annotated[str | None, typer.Option(help="Shell to show completion for")] = None,
) -> None:
    """Show the completion script without installing."""
    detected_shell = shell or _detect_shell()

    if detected_shell in TYPER_SUPPORTED_SHELLS:
        try:
            import click.shell_completion

            shell_map = {
                "bash": "bash",
                "zsh": "zsh",
                "fish": "fish",
                "powershell": "powershell",
                "pwsh": "powershell",
            }
            shell_name = shell_map.get(detected_shell, detected_shell)
            shell_complete = click.shell_completion.get_completion_class(shell_name)
            if shell_complete:
                comp = shell_complete(app, {}, "emma", "_EMMA_COMPLETE")
                console.print(comp.source())
                return
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    # For other shells, show carapace command
    if shutil.which("carapace"):
        spec_path = _get_carapace_spec_path()
        if spec_path:
            try:
                result = subprocess.run(
                    ["carapace", "emma", "export", "--spec", str(spec_path), detected_shell],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    console.print(result.stdout)
                    return
                else:
                    console.print(f"[red]carapace error: {result.stderr}[/red]")
            except Exception as e:
                console.print(f"[red]Error running carapace: {e}[/red]")

    console.print(f"[red]Cannot generate completion for shell: {detected_shell}[/red]")
    raise typer.Exit(1)


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


audit_app = typer.Typer(help="View audit log of email operations", no_args_is_help=True)
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


draft_app = typer.Typer(help="Manage draft replies", no_args_is_help=True)
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


# ─── Service Commands ────────────────────────────────────────────────────────


service_app = typer.Typer(help="Manage Emma background service", no_args_is_help=True)
app.add_typer(service_app, name="service")


@service_app.command("start")
def service_start(
    foreground: Annotated[bool, typer.Option("--foreground", "-f", help="Run in foreground")] = False,
) -> None:
    """Start the Emma background service.

    By default runs as a daemon. Use --foreground to run interactively.
    """
    settings = load_settings()

    if not settings.service.enabled:
        console.print("[yellow]Service is disabled in configuration.[/yellow]")
        console.print("Set 'service.enabled: true' in config.yaml to enable.")
        raise typer.Exit(1)

    from email_agent.service import EmmaService

    service = EmmaService(settings)

    if foreground:
        console.print("[cyan]Starting Emma service in foreground...[/cyan]")
        console.print("Press Ctrl+C to stop.\n")
        asyncio.run(service.start())
    else:
        # For background, we'd normally daemonize, but recommend systemd
        console.print("[yellow]Background mode not implemented.[/yellow]")
        console.print("Use --foreground or configure as a systemd service.")
        console.print("\nFor NixOS/Home Manager, enable the systemd service:")
        console.print("  services.emma.service.enable = true")


@service_app.command("stop")
def service_stop() -> None:
    """Stop the Emma background service.

    Only works with systemd-managed services.
    """
    console.print("[yellow]Direct stop not supported.[/yellow]")
    console.print("If running in foreground, use Ctrl+C.")
    console.print("For systemd service: systemctl --user stop emma")


@service_app.command("status")
def service_status() -> None:
    """Show Emma service status and statistics."""
    settings = load_settings()

    console.print("[bold cyan]Emma Service Status[/bold cyan]\n")

    # Configuration status
    console.print("[bold]Configuration:[/bold]")
    console.print(f"  Service enabled: {'[green]Yes[/green]' if settings.service.enabled else '[red]No[/red]'}")
    console.print(f"  Monitor enabled: {'[green]Yes[/green]' if settings.service.monitor.enabled else '[red]No[/red]'}")
    console.print(f"  Digest enabled: {'[green]Yes[/green]' if settings.service.digest.enabled else '[red]No[/red]'}")
    console.print(f"  Polling interval: {settings.service.polling_interval}s")
    console.print(f"  Digest schedule: {', '.join(settings.service.digest.schedule)}")

    # Statistics from state
    from email_agent.service import ServiceState
    state = ServiceState(settings.db_path)
    stats = state.get_stats()

    console.print("\n[bold]Statistics:[/bold]")
    console.print(f"  Total processed emails: {stats['total_processed_emails']}")
    console.print(f"  Emails last 24h: {stats['emails_last_24h']}")
    console.print(f"  Total digests: {stats['total_digests']}")
    console.print(f"  Total action items: {stats['total_action_items']}")

    if stats.get('action_items_by_status'):
        console.print("  Action items by status:")
        for status, count in stats['action_items_by_status'].items():
            console.print(f"    - {status}: {count}")

    if stats.get('last_digest'):
        console.print(f"  Last digest: {stats['last_digest']}")


@service_app.command("run-once")
def service_run_once(
    monitor: Annotated[bool, typer.Option("--monitor", "-m", help="Run monitor cycle")] = True,
    digest: Annotated[bool, typer.Option("--digest", "-d", help="Generate digest")] = False,
) -> None:
    """Run service jobs once without starting daemon.

    Useful for testing or cron-based scheduling.
    """
    settings = load_settings()

    from email_agent.service import EmmaService

    service = EmmaService(settings)

    async def _run() -> None:
        results = await service.run_once(run_monitor=monitor, run_digest=digest)

        if "monitor" in results:
            console.print("\n[bold cyan]Monitor Results:[/bold cyan]")
            m = results["monitor"]
            console.print(f"  Emails found: {m.get('emails_found', 0)}")
            console.print(f"  Emails processed: {m.get('emails_processed', 0)}")
            console.print(f"  Action items created: {m.get('action_items_created', 0)}")
            console.print(f"  Errors: {m.get('errors', 0)}")

        if "digest" in results:
            console.print("\n[bold cyan]Digest Results:[/bold cyan]")
            d = results["digest"]
            if d.get("generated") is False:
                console.print(f"  No digest generated: {d.get('reason', 'unknown')}")
            else:
                console.print(f"  Digest ID: {d.get('id', 'N/A')[:8]}")
                console.print(f"  Email count: {d.get('email_count', 0)}")
                console.print(f"  Delivered: {'[green]Yes[/green]' if d.get('delivered') else '[red]No[/red]'}")

    asyncio.run(_run())


# ─── Digest Commands ─────────────────────────────────────────────────────────


digest_app = typer.Typer(help="Manage email digests", no_args_is_help=True)
app.add_typer(digest_app, name="digest")


@digest_app.command("generate")
def digest_generate(
    hours: Annotated[int, typer.Option("--hours", "-h", help="Hours to include in digest")] = 12,
    deliver: Annotated[bool, typer.Option("--deliver", "-d", help="Deliver after generating")] = True,
    force: Annotated[bool, typer.Option("--force", "-f", help="Generate even if under threshold")] = False,
) -> None:
    """Generate an email digest now.

    Summarizes processed emails from the specified period.
    """
    settings = load_settings()

    from email_agent.service import DigestGenerator, ServiceState
    from email_agent.processors.llm import LLMProcessor

    state = ServiceState(settings.db_path)
    llm_processor = None
    if settings.llm:
        try:
            api_key = settings.anthropic_api_key if settings.llm.provider == "anthropic" else None
            llm_processor = LLMProcessor(settings.llm, api_key)
        except Exception:
            pass

    generator = DigestGenerator(settings, state, llm_processor)

    async def _generate() -> None:
        console.print(f"Generating digest for last {hours} hours...")

        digest = await generator.generate(period_hours=hours, force=force)

        if not digest:
            console.print("[yellow]No digest generated (no emails or below threshold).[/yellow]")
            console.print("Use --force to generate anyway.")
            return

        console.print(f"\n[green]Digest generated: {digest.id[:8]}[/green]")
        console.print(f"  Period: {digest.period_start.strftime('%Y-%m-%d %H:%M')} to {digest.period_end.strftime('%Y-%m-%d %H:%M')}")
        console.print(f"  Emails: {digest.email_count}")

        if deliver:
            console.print("\nDelivering digest...")
            success = await generator.deliver(digest)
            if success:
                console.print("[green]Digest delivered successfully.[/green]")
            else:
                console.print("[red]Digest delivery failed.[/red]")

    asyncio.run(_generate())


@digest_app.command("list")
def digest_list(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max digests to show")] = 10,
) -> None:
    """List recent digests."""
    settings = load_settings()

    from email_agent.service import ServiceState

    state = ServiceState(settings.db_path)
    digests = state.list_digests(limit=limit)

    if not digests:
        console.print("[yellow]No digests found.[/yellow]")
        return

    table = Table(title="Recent Digests")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Created", width=19)
    table.add_column("Period", width=25)
    table.add_column("Emails", width=8)
    table.add_column("Status", width=10)

    for digest in digests:
        period = f"{digest.period_start.strftime('%m/%d %H:%M')} - {digest.period_end.strftime('%H:%M')}"
        status = digest.delivery_status.value
        if status == "delivered":
            status = f"[green]{status}[/green]"
        elif status == "failed":
            status = f"[red]{status}[/red]"
        else:
            status = f"[yellow]{status}[/yellow]"

        table.add_row(
            digest.id[:8],
            digest.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            period,
            str(digest.email_count),
            status,
        )

    console.print(table)


@digest_app.command("show")
def digest_show(
    ctx: typer.Context,
    digest_id: Annotated[str, typer.Argument(help="Digest ID (or prefix)")],
) -> None:
    """Show digest content."""
    settings = load_settings()

    from email_agent.service import ServiceState

    state = ServiceState(settings.db_path)

    # Find by full ID or prefix
    digest = state.get_digest(digest_id)
    if not digest:
        # Try prefix match
        for d in state.list_digests(limit=100):
            if d.id.startswith(digest_id):
                digest = d
                break

    if not digest:
        _error_with_help(ctx, f"Digest not found: {digest_id}")

    console.print(Panel(f"[bold]Digest: {digest.id}[/bold]"))
    console.print(f"[bold]Created:[/bold] {digest.created_at}")
    console.print(f"[bold]Period:[/bold] {digest.period_start} to {digest.period_end}")
    console.print(f"[bold]Emails:[/bold] {digest.email_count}")
    console.print(f"[bold]Status:[/bold] {digest.delivery_status.value}")

    console.print("\n[bold cyan]Summary:[/bold cyan]")
    console.print(digest.summary)

    if digest.raw_content:
        console.print("\n[bold cyan]Full Content:[/bold cyan]")
        console.print("─" * 60)
        console.print(digest.raw_content)


# ─── Action Item Commands ────────────────────────────────────────────────────


actions_app = typer.Typer(help="Manage action items extracted from emails", no_args_is_help=True)
app.add_typer(actions_app, name="actions")


@actions_app.command("list")
def actions_list(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option("--status", "-s", help="Filter by status (pending/in_progress/completed/dismissed)")] = None,
    priority: Annotated[str | None, typer.Option("--priority", "-p", help="Filter by priority (low/normal/high/urgent)")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max items to show")] = 20,
) -> None:
    """List action items."""
    settings = load_settings()

    from email_agent.service import ServiceState

    state = ServiceState(settings.db_path)

    filter_status = None
    if status:
        try:
            filter_status = ActionItemStatus(status)
        except ValueError:
            valid = ", ".join(s.value for s in ActionItemStatus)
            _error_with_help(ctx, f"Unknown status: {status}. Valid: {valid}")

    filter_priority = None
    if priority:
        try:
            filter_priority = EmailPriority(priority)
        except ValueError:
            valid = ", ".join(p.value for p in EmailPriority)
            _error_with_help(ctx, f"Unknown priority: {priority}. Valid: {valid}")

    items = state.list_action_items(
        status=filter_status,
        priority=filter_priority,
        limit=limit,
    )

    if not items:
        console.print("[yellow]No action items found.[/yellow]")
        return

    table = Table(title="Action Items")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Pri", width=4)
    table.add_column("Status", width=12)
    table.add_column("Due", width=10)
    table.add_column("Title", width=40)

    for item in items:
        pri = item.priority.value[0].upper()
        if item.priority == EmailPriority.URGENT:
            pri = f"[red]{pri}[/red]"
        elif item.priority == EmailPriority.HIGH:
            pri = f"[yellow]{pri}[/yellow]"

        status_str = item.status.value
        if item.status == ActionItemStatus.PENDING:
            status_str = f"[yellow]{status_str}[/yellow]"
        elif item.status == ActionItemStatus.COMPLETED:
            status_str = f"[green]{status_str}[/green]"
        elif item.status == ActionItemStatus.DISMISSED:
            status_str = f"[dim]{status_str}[/dim]"

        due = item.due_date.strftime("%Y-%m-%d") if item.due_date else "-"
        title = item.title[:38] + "..." if len(item.title) > 40 else item.title

        table.add_row(item.id[:8], pri, status_str, due, title)

    console.print(table)


@actions_app.command("show")
def actions_show(
    ctx: typer.Context,
    item_id: Annotated[str, typer.Argument(help="Action item ID (or prefix)")],
) -> None:
    """Show action item details."""
    settings = load_settings()

    from email_agent.service import ServiceState

    state = ServiceState(settings.db_path)

    # Find by full ID or prefix
    item = state.get_action_item(item_id)
    if not item:
        for i in state.list_action_items(limit=100):
            if i.id.startswith(item_id):
                item = i
                break

    if not item:
        _error_with_help(ctx, f"Action item not found: {item_id}")

    console.print(Panel(f"[bold]Action Item: {item.id}[/bold]"))
    console.print(f"[bold]Title:[/bold] {item.title}")
    console.print(f"[bold]Status:[/bold] {item.status.value}")
    console.print(f"[bold]Priority:[/bold] {item.priority.value}")
    console.print(f"[bold]Urgency:[/bold] {item.urgency}")
    console.print(f"[bold]Created:[/bold] {item.created_at}")

    if item.due_date:
        console.print(f"[bold]Due Date:[/bold] {item.due_date}")

    if item.completed_at:
        console.print(f"[bold]Completed:[/bold] {item.completed_at}")

    if item.description:
        console.print(f"\n[bold cyan]Description:[/bold cyan]")
        console.print(item.description)

    if item.metadata:
        console.print(f"\n[bold cyan]Metadata:[/bold cyan]")
        console.print(json.dumps(item.metadata, indent=2))


@actions_app.command("complete")
def actions_complete(
    ctx: typer.Context,
    item_id: Annotated[str, typer.Argument(help="Action item ID (or prefix)")],
) -> None:
    """Mark an action item as completed."""
    settings = load_settings()

    from email_agent.service import ServiceState

    state = ServiceState(settings.db_path)

    # Find by full ID or prefix
    full_id = item_id
    item = state.get_action_item(item_id)
    if not item:
        for i in state.list_action_items(limit=100):
            if i.id.startswith(item_id):
                full_id = i.id
                item = i
                break

    if not item:
        _error_with_help(ctx, f"Action item not found: {item_id}")

    if item.status == ActionItemStatus.COMPLETED:
        console.print("[yellow]Action item is already completed.[/yellow]")
        return

    success = state.update_action_status(full_id, ActionItemStatus.COMPLETED)
    if success:
        console.print(f"[green]Action item completed: {full_id[:8]}[/green]")
    else:
        console.print("[red]Failed to update action item.[/red]")


@actions_app.command("dismiss")
def actions_dismiss(
    ctx: typer.Context,
    item_id: Annotated[str, typer.Argument(help="Action item ID (or prefix)")],
) -> None:
    """Dismiss an action item."""
    settings = load_settings()

    from email_agent.service import ServiceState

    state = ServiceState(settings.db_path)

    # Find by full ID or prefix
    full_id = item_id
    item = state.get_action_item(item_id)
    if not item:
        for i in state.list_action_items(limit=100):
            if i.id.startswith(item_id):
                full_id = i.id
                item = i
                break

    if not item:
        _error_with_help(ctx, f"Action item not found: {item_id}")

    if item.status == ActionItemStatus.DISMISSED:
        console.print("[yellow]Action item is already dismissed.[/yellow]")
        return

    success = state.update_action_status(full_id, ActionItemStatus.DISMISSED)
    if success:
        console.print(f"[green]Action item dismissed: {full_id[:8]}[/green]")
    else:
        console.print("[red]Failed to update action item.[/red]")


if __name__ == "__main__":
    app()
