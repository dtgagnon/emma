{
  pkgs,
  self,
  system,
}:
let
  python = pkgs.python313;
in
{
  # Development shell - uses PYTHONPATH for live reloading
  default = pkgs.mkShell {
    packages = with pkgs; [
      # Python with packages
      (python.withPackages (ps: with ps; [
        # Email handling
        aiosmtplib       # Async SMTP
        imapclient       # Better IMAP client

        # LLM integrations
        anthropic        # Claude API
        ollama           # Ollama native client
        openai           # OpenAI-compatible APIs

        # CLI and config
        typer            # CLI framework
        rich             # Pretty output
        pyyaml           # Config files
        pydantic         # Data validation
        pydantic-settings # Settings management

        # Async and scheduling
        aiofiles         # Async file I/O
        apscheduler      # Task scheduling

        # Database
        sqlalchemy       # ORM
        aiosqlite        # Async SQLite

        # Messaging
        matrix-nio       # Matrix protocol client

        # Testing
        pytest
        pytest-asyncio

        # Dev tools
        black
        ruff
        mypy
      ]))

      # Email CLI tools
      notmuch          # Email indexing and search
      mblaze           # Maildir utilities
      isync            # IMAP sync (mbsync)
      msmtp            # SMTP sending

      # General tools
      ripgrep
      jq
    ];

    shellHook = ''
      export PYTHONPATH="$PWD/src:$PYTHONPATH"

      echo ""
      echo "Email Automation Platform"
      echo "========================================"
      echo ""
      echo "Commands:"
      echo "  python -m email_agent.cli   Run the CLI"
      echo "  pytest                      Run tests"
      echo "  ruff check src/             Lint code"
      echo "  black src/                  Format code"
      echo ""
      echo "For installed 'emma' command: nix develop .#installed"
      echo ""
    '';
  };

  # Installed shell - emma CLI is available directly
  installed = pkgs.mkShell {
    packages = with pkgs; [
      self.packages.${system}.emma

      # Email CLI tools
      notmuch
      mblaze
      isync
      msmtp

      # Dev tools
      (python.withPackages (ps: with ps; [
        pytest
        pytest-asyncio
        black
        ruff
        mypy
      ]))

      ripgrep
      jq
    ];

    shellHook = ''
      echo ""
      echo "EMMA - Email Automation Platform (installed)"
      echo "============================================="
      echo ""
      echo "Commands:"
      echo "  emma --help         Show CLI help"
      echo "  emma audit list     View audit log"
      echo "  emma draft list     View pending drafts"
      echo "  emma email list     List emails"
      echo ""
    '';
  };
}
