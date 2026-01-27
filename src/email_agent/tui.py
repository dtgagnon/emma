"""Terminal UI components for interactive email selection."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.table import Table

from email_agent.models import Email

console = Console()


def select_email(emails: list[Email]) -> Email | None:
    """Interactive email selector. Uses fzf if available, else numbered list.

    Args:
        emails: List of Email objects to select from

    Returns:
        Selected Email object, or None if cancelled
    """
    if not emails:
        console.print("[yellow]No emails to display.[/yellow]")
        return None

    if shutil.which("fzf"):
        return _select_with_fzf(emails)
    return _select_with_prompt(emails)


def _sanitize_for_fzf(text: str) -> str:
    """Remove/replace characters that would break fzf field parsing."""
    # Replace tabs and newlines with spaces (these break fzf delimiter parsing)
    return text.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _format_email_line(idx: int, email: Email) -> str:
    """Format a single email for fzf display.

    Format: idx\tDate\tFrom\tSubject
    """
    date_str = email.date.strftime("%Y-%m-%d %H:%M") if email.date else "Unknown"
    from_addr = _sanitize_for_fzf(email.from_addr)
    subject = _sanitize_for_fzf(email.subject)
    return f"{idx}\t{date_str}\t{from_addr}\t{subject}"


def _select_with_fzf(emails: list[Email]) -> Email | None:
    """Select email using fzf with preview.

    Returns:
        Selected Email object, or None if cancelled
    """
    # Build the list for fzf
    lines = [_format_email_line(i, email) for i, email in enumerate(emails)]
    input_text = "\n".join(lines)

    # Create a temporary file with email bodies for preview
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Write email bodies to temp files for preview
        for i, email in enumerate(emails):
            body_file = tmppath / f"{i}.txt"
            preview_content = _format_email_preview(email)
            body_file.write_text(preview_content)

        # Build fzf command with preview
        preview_cmd = f"cat {tmppath}/{{1}}.txt"

        try:
            result = subprocess.run(
                [
                    "fzf",
                    "--delimiter=\t",
                    "--with-nth=2..",  # Display from column 2 onwards (skip index)
                    "--preview",
                    preview_cmd,
                    "--preview-window=right:50%:wrap",
                    "--header=Date\t\t\tFrom\t\t\t\tSubject",
                    "--header-lines=0",
                    "--ansi",
                    "--no-mouse",
                    "--bind=ctrl-c:abort,esc:abort",
                ],
                input=input_text,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                # User cancelled (Ctrl-C or Esc)
                return None

            # Parse selected line to get index
            selected = result.stdout.strip()
            if not selected:
                return None

            # First field is the index
            idx_str = selected.split("\t")[0]
            idx = int(idx_str)
            return emails[idx]

        except (subprocess.SubprocessError, ValueError, IndexError):
            console.print("[yellow]fzf selection failed, falling back to numbered list[/yellow]")
            return _select_with_prompt(emails)


def _format_email_preview(email: Email) -> str:
    """Format email for fzf preview window."""
    lines = [
        f"Subject: {email.subject}",
        f"From: {email.from_addr}",
        f"To: {', '.join(email.to_addrs)}",
    ]
    if email.cc_addrs:
        lines.append(f"CC: {', '.join(email.cc_addrs)}")
    lines.append(f"Date: {email.date}")
    lines.append(f"Folder: {email.folder}")
    if email.attachments:
        lines.append(f"Attachments: {len(email.attachments)}")
    lines.append("")
    lines.append("─" * 50)
    lines.append("")

    # Truncate body for preview
    body = email.body_text[:2000] if email.body_text else "(no body)"
    lines.append(body)

    return "\n".join(lines)


def _select_with_prompt(emails: list[Email]) -> Email | None:
    """Select email using numbered list and prompt.

    Returns:
        Selected Email object, or None if cancelled
    """
    # Display table
    table = Table(title="Select an Email")
    table.add_column("#", style="dim", width=4)
    table.add_column("Date", width=12)
    table.add_column("From", width=30)
    table.add_column("Subject")

    for i, email in enumerate(emails, start=1):
        date_str = email.date.strftime("%Y-%m-%d") if email.date else "?"
        from_addr = email.from_addr[:28] + ".." if len(email.from_addr) > 30 else email.from_addr
        subject = email.subject[:45] + "..." if len(email.subject) > 48 else email.subject
        table.add_row(str(i), date_str, from_addr, subject)

    console.print(table)
    console.print()

    # Prompt for selection
    while True:
        try:
            response = console.input("[bold]Enter number (or 'q' to quit):[/bold] ")
            response = response.strip().lower()

            if response in ("q", "quit", "exit", ""):
                return None

            num = int(response)
            if 1 <= num <= len(emails):
                return emails[num - 1]
            else:
                console.print(f"[red]Please enter a number between 1 and {len(emails)}[/red]")

        except ValueError:
            console.print("[red]Please enter a valid number or 'q' to quit[/red]")
        except (KeyboardInterrupt, EOFError):
            console.print()
            return None
