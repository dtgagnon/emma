{
  config,
  lib,
  pkgs,
  ...
}:

with lib;

let
  cfg = config.programs.emma;
  settingsFormat = pkgs.formats.yaml { };

  # Submodule for IMAP account configuration
  imapAccountType = types.submodule {
    options = {
      host = mkOption {
        type = types.str;
        description = "IMAP server hostname";
        example = "imap.example.com";
      };

      port = mkOption {
        type = types.port;
        default = 993;
        description = "IMAP server port";
      };

      username = mkOption {
        type = types.str;
        description = "IMAP username";
      };

      passwordFile = mkOption {
        type = types.path;
        description = ''
          Path to file containing the IMAP password.
          Compatible with sops-nix, agenix, or manually created secret files.
        '';
        example = "/run/secrets/email-password";
      };

      useSsl = mkOption {
        type = types.bool;
        default = true;
        description = "Use SSL/TLS for connection";
      };

      folders = mkOption {
        type = types.listOf types.str;
        default = [ "INBOX" ];
        description = "IMAP folders to monitor";
      };
    };
  };

  # Submodule for SMTP account configuration
  smtpAccountType = types.submodule {
    options = {
      host = mkOption {
        type = types.str;
        description = "SMTP server hostname";
        example = "smtp.example.com";
      };

      port = mkOption {
        type = types.port;
        default = 587;
        description = "SMTP server port";
      };

      username = mkOption {
        type = types.str;
        description = "SMTP username";
      };

      passwordFile = mkOption {
        type = types.path;
        description = ''
          Path to file containing the SMTP password.
          Compatible with sops-nix, agenix, or manually created secret files.
        '';
        example = "/run/secrets/email-password";
      };

      useTls = mkOption {
        type = types.bool;
        default = true;
        description = "Use STARTTLS for connection";
      };
    };
  };

  # Submodule for Maildir account configuration
  # Key is the email address, all fields are optional with sensible defaults
  maildirAccountType = types.submodule {
    options = {
      accountName = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Display name for this account (defaults to email domain)";
        example = "work";
      };

      path = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Path to the Maildir directory (defaults to ~/Mail/<email>)";
        example = "~/Mail/user@example.com";
      };

      default = mkOption {
        type = types.bool;
        default = false;
        description = "Mark this as the default/primary account";
      };
    };
  };

  # Submodule for digest delivery configuration
  digestDeliveryType = types.submodule {
    options = {
      type = mkOption {
        type = types.enum [ "file" ];
        default = "file";
        description = "Delivery method type (currently only 'file' is supported)";
      };

      outputDir = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Output directory for digest files (defaults to ~/.local/share/emma/digests/)";
      };

      format = mkOption {
        type = types.enum [ "markdown" "html" "text" ];
        default = "markdown";
        description = "Output format for digests";
      };
    };
  };

  # Convert Nix attrset to Python config format (camelCase -> snake_case)
  toSnakeCase = str:
    let
      chars = stringToCharacters str;
      convert = c:
        if c >= "A" && c <= "Z"
        then "_${toLower c}"
        else c;
    in
    concatStrings (map convert chars);

  # Convert IMAP account settings (handling passwordFile specially)
  convertImapAccount = name: account: {
    host = account.host;
    port = account.port;
    username = account.username;
    # Note: password is read from file at runtime
    password_file = toString account.passwordFile;
    use_ssl = account.useSsl;
    folders = account.folders;
  };

  # Convert SMTP account settings
  convertSmtpAccount = name: account: {
    host = account.host;
    port = account.port;
    username = account.username;
    password_file = toString account.passwordFile;
    use_tls = account.useTls;
  };

  # Convert Maildir account settings - only include non-default values
  convertMaildirAccount = email: account:
    lib.filterAttrs (_: v: v != null) {
      account_name = account.accountName;
      path = account.path;
      default = if account.default then true else null;
    };

  # Convert digest delivery settings
  convertDigestDelivery = delivery: {
    type = delivery.type;
    output_dir = delivery.outputDir;
    format = delivery.format;
  };

  # Build the final settings structure
  finalSettings = {
    llm = {
      provider = cfg.settings.llm.provider;
      model = cfg.settings.llm.model;
      max_tokens = cfg.settings.llm.maxTokens;
      temperature = cfg.settings.llm.temperature;
      ollama_base_url = cfg.settings.llm.ollamaBaseUrl;
      ollama_context_length = cfg.settings.llm.ollamaContextLength;
    };

    imap_accounts = mapAttrs convertImapAccount cfg.settings.imapAccounts;
    smtp_accounts = mapAttrs convertSmtpAccount cfg.settings.smtpAccounts;
    maildir_accounts = mapAttrs convertMaildirAccount cfg.settings.maildirAccounts;

    mxroute = {
      enabled = cfg.settings.mxroute.enable;
      domain = cfg.settings.mxroute.domain;
    };

    batch_size = cfg.settings.batchSize;
    polling_interval = cfg.settings.pollingInterval;

    # Service configuration
    service = {
      enabled = cfg.service.enable;
      polling_interval = cfg.service.pollingInterval;
      monitor = {
        enabled = cfg.service.monitor.enable;
        sources = cfg.service.monitor.sources;
        folders = cfg.service.monitor.folders;
        auto_classify = cfg.service.monitor.autoClassify;
        apply_rules = cfg.service.monitor.applyRules;
        extract_actions = cfg.service.monitor.extractActions;
      };
      digest = {
        enabled = cfg.service.digest.enable;
        schedule = cfg.service.digest.schedule;
        period_hours = cfg.service.digest.periodHours;
        min_emails = cfg.service.digest.minEmails;
        include_action_items = cfg.service.digest.includeActionItems;
        delivery = map convertDigestDelivery cfg.service.digest.delivery;
      };
      action_items = {
        auto_extract = cfg.service.actionItems.autoExtract;
      };
    };
  };

in
{
  options.programs.emma = {
    enable = mkEnableOption "emma, an email automation platform with LLM processing";

    package = mkOption {
      type = types.package;
      default = pkgs.callPackage ./package.nix { };
      description = "The emma package to use";
    };

    settings = {
      llm = {
        provider = mkOption {
          type = types.enum [ "ollama" "anthropic" ];
          default = "ollama";
          description = "LLM provider to use";
        };

        model = mkOption {
          type = types.str;
          default = "gpt-oss:20b";
          description = "Model name/ID for the LLM provider";
          example = "claude-3-haiku-20240307";
        };

        maxTokens = mkOption {
          type = types.int;
          default = 1024;
          description = "Maximum tokens for LLM responses";
        };

        temperature = mkOption {
          type = types.float;
          default = 0.3;
          description = "Temperature for LLM responses (0.0-1.0)";
        };

        ollamaBaseUrl = mkOption {
          type = types.str;
          default = "http://localhost:11434";
          description = "Base URL for Ollama API";
        };

        ollamaContextLength = mkOption {
          type = types.int;
          default = 24576;
          description = "Context length (num_ctx) for Ollama models";
        };
      };

      imapAccounts = mkOption {
        type = types.attrsOf imapAccountType;
        default = { };
        description = "IMAP email accounts to monitor";
        example = literalExpression ''
          {
            personal = {
              host = "imap.example.com";
              username = "user@example.com";
              passwordFile = config.sops.secrets.email-password.path;
              folders = [ "INBOX" "Sent" ];
            };
          }
        '';
      };

      smtpAccounts = mkOption {
        type = types.attrsOf smtpAccountType;
        default = { };
        description = "SMTP accounts for sending email";
        example = literalExpression ''
          {
            personal = {
              host = "smtp.example.com";
              username = "user@example.com";
              passwordFile = config.sops.secrets.email-password.path;
            };
          }
        '';
      };

      maildirAccounts = mkOption {
        type = types.attrsOf maildirAccountType;
        default = { };
        description = ''
          Local Maildir accounts to process.
          Key is the email address. All fields are optional with sensible defaults.
        '';
        example = literalExpression ''
          {
            "user@gmail.com" = { };  # Uses defaults: ~/Mail/user@gmail.com, name="gmail"
            "user@work.com" = {
              accountName = "work";  # Override derived name
              default = true;        # Mark as primary
            };
          }
        '';
      };

      mxroute = {
        enable = mkEnableOption "MXroute MCP integration";

        domain = mkOption {
          type = types.nullOr types.str;
          default = null;
          description = "MXroute domain to manage";
          example = "example.com";
        };
      };

      batchSize = mkOption {
        type = types.int;
        default = 50;
        description = "Number of emails to process per batch";
      };

      pollingInterval = mkOption {
        type = types.int;
        default = 300;
        description = "Polling interval in seconds for checking new emails";
      };
    };

    # Background service configuration
    service = {
      enable = mkEnableOption "Emma background service for email monitoring and automation";

      pollingInterval = mkOption {
        type = types.int;
        default = 300;
        description = "Polling interval in seconds for the service";
      };

      monitor = {
        enable = mkOption {
          type = types.bool;
          default = true;
          description = "Enable email monitoring";
        };

        sources = mkOption {
          type = types.listOf types.str;
          default = [ ];
          description = "Email sources to monitor (empty = all configured sources)";
        };

        folders = mkOption {
          type = types.listOf types.str;
          default = [ "INBOX" ];
          description = "Folders to monitor for new emails";
        };

        autoClassify = mkOption {
          type = types.bool;
          default = true;
          description = "Automatically classify emails using LLM";
        };

        applyRules = mkOption {
          type = types.bool;
          default = true;
          description = "Apply automation rules to emails";
        };

        extractActions = mkOption {
          type = types.bool;
          default = true;
          description = "Extract action items from emails";
        };
      };

      digest = {
        enable = mkOption {
          type = types.bool;
          default = true;
          description = "Enable email digest generation";
        };

        schedule = mkOption {
          type = types.listOf types.str;
          default = [ "08:00" "20:00" ];
          description = "Times to generate digests (24h format)";
          example = [ "08:00" "12:00" "18:00" ];
        };

        periodHours = mkOption {
          type = types.int;
          default = 12;
          description = "Hours to include in each digest";
        };

        minEmails = mkOption {
          type = types.int;
          default = 1;
          description = "Minimum emails required to generate a digest";
        };

        includeActionItems = mkOption {
          type = types.bool;
          default = true;
          description = "Include action items in digests";
        };

        delivery = mkOption {
          type = types.listOf digestDeliveryType;
          default = [ ];
          description = "Delivery methods for digests (defaults to file if empty)";
          example = literalExpression ''
            [
              {
                type = "file";
                format = "markdown";
                outputDir = "~/.local/share/emma/digests";
              }
            ]
          '';
        };
      };

      actionItems = {
        autoExtract = mkOption {
          type = types.bool;
          default = true;
          description = "Automatically extract action items from processed emails";
        };
      };
    };
  };

  config = mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.configFile."emma/config.yaml".source =
      settingsFormat.generate "emma-config.yaml" finalSettings;

    # Systemd user service for Emma background processing
    systemd.user.services.emma = mkIf cfg.service.enable {
      Unit = {
        Description = "Emma Email Automation Service";
        After = [ "network.target" ];
      };

      Service = {
        Type = "simple";
        ExecStart = "${cfg.package}/bin/emma service start --foreground";
        Restart = "on-failure";
        RestartSec = "10s";

        # Environment - include notmuch in PATH for email tagging
        Environment = [
          "HOME=%h"
          "PATH=${pkgs.notmuch}/bin"
        ];

        # Security hardening
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        ReadWritePaths = [
          "%h/.local/share/emma"
          "%h/.config/emma"
          "%h/Mail/.notmuch"  # notmuch database for tagging
        ];
        PrivateTmp = true;
      };

      Install = {
        WantedBy = [ "default.target" ];
      };
    };
  };
}
