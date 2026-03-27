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
        type = types.enum [ "file" "matrix" ];
        default = "file";
        description = "Delivery method type: 'file' or 'matrix'";
      };

      # File delivery options
      outputDir = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Output directory for digest files (defaults to ~/.local/share/emma/digests/)";
      };

      format = mkOption {
        type = types.enum [ "markdown" "html" "text" ];
        default = "markdown";
        description = "Output format for file delivery";
      };

      # Matrix delivery options
      matrixEnvFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          Path to an env file containing Matrix connection details.
          The file must contain three KEY=value lines:
            HOMESERVER=https://matrix.example.com
            ROOM_ID=!abc123:example.com
            ACCESS_TOKEN=syt_...
          Compatible with sops-nix secrets.
        '';
        example = "/run/secrets/emma-matrix-env";
      };

      matrixFormat = mkOption {
        type = types.enum [ "html" "markdown" ];
        default = "html";
        description = "Message format for Matrix delivery (html renders rich text in most clients)";
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
  convertDigestDelivery = delivery:
    lib.filterAttrs (_: v: v != null) ({
      type = delivery.type;
    } // lib.optionalAttrs (delivery.type == "file") {
      output_dir = delivery.outputDir;
      format = delivery.format;
    } // lib.optionalAttrs (delivery.type == "matrix") {
      matrix_env_file = if delivery.matrixEnvFile != null
        then toString delivery.matrixEnvFile
        else null;
      matrix_format = delivery.matrixFormat;
    });

  # Build the final settings structure
  finalSettings = {
    llm = {
      provider = cfg.settings.llm.provider;
      model = cfg.settings.llm.model;
      max_tokens = cfg.settings.llm.maxTokens;
      base_url = cfg.settings.llm.baseUrl;
      context_length = cfg.settings.llm.contextLength;
      tasks = lib.mapAttrs (_name: task:
        { max_tokens = task.maxTokens; }
        // lib.filterAttrs (_: v: v != null) {
          provider = task.provider;
          model = task.model;
          base_url = task.baseUrl;
          context_length = task.contextLength;
        }
      ) cfg.settings.llm.tasks;
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
      log_level = cfg.service.logLevel;
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
        min_emails = cfg.service.digest.minEmails;
        include_action_items = cfg.service.digest.includeActionItems;
        delivery = map convertDigestDelivery cfg.service.digest.delivery;
      };
      action_items = {
        auto_extract = cfg.service.actionItems.autoExtract;
        todo_file = cfg.service.actionItems.todoFile;
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
          type = types.enum [ "ollama" "anthropic" "openai" ];
          default = "ollama";
          description = "LLM provider to use (openai works with any OpenAI-compatible API)";
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

        baseUrl = mkOption {
          type = types.str;
          default = "http://localhost:11434";
          description = "Base URL for the LLM API";
        };

        contextLength = mkOption {
          type = types.int;
          default = 24576;
          description = "Context window size for the model";
        };

        tasks = let
          mkTaskOverrideType = { defaultMaxTokens }: types.submodule {
            options = {
              provider = mkOption {
                type = types.nullOr (types.enum [ "ollama" "anthropic" "openai" ]);
                default = null;
                description = "Override LLM provider for this task";
              };
              model = mkOption {
                type = types.nullOr types.str;
                default = null;
                description = "Override model for this task";
              };
              maxTokens = mkOption {
                type = types.int;
                default = defaultMaxTokens;
                description = "Maximum output tokens for this task";
              };
              baseUrl = mkOption {
                type = types.nullOr types.str;
                default = null;
                description = "Override base URL for this task";
              };
              contextLength = mkOption {
                type = types.nullOr types.int;
                default = null;
                description = "Override context length for this task";
              };
            };
          };
        in {
          classify = mkOption {
            type = mkTaskOverrideType { defaultMaxTokens = 150; };
            default = { };
            description = "LLM overrides for email classification";
          };
          analyze = mkOption {
            type = mkTaskOverrideType { defaultMaxTokens = 800; };
            default = { };
            description = "LLM overrides for email analysis (summary + action item extraction)";
          };
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

      logLevel = mkOption {
        type = types.enum [ "DEBUG" "INFO" "WARNING" "ERROR" ];
        default = "INFO";
        description = "Log level for the Emma service (DEBUG, INFO, WARNING, ERROR)";
      };

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

        todoFile = mkOption {
          type = types.str;
          default = "~/TODO.json";
          description = "Path to TODO.json file for persisting action items";
          example = "~/Documents/TODO.json";
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
        ExecStart = "${cfg.package}/bin/emma service start --foreground --log-level ${cfg.service.logLevel}";
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
          "%h/TODO.json"      # Action items persistence
        ];
        PrivateTmp = true;
      };

      Install = {
        WantedBy = [ "default.target" ];
      };
    };
  };
}
