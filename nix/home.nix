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
  maildirAccountType = types.submodule {
    options = {
      path = mkOption {
        type = types.str;
        description = "Path to the Maildir directory";
        example = "~/.thunderbird/profile/ImapMail/server";
      };

      accountName = mkOption {
        type = types.str;
        default = "local";
        description = "Name for this maildir account";
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

  # Convert Maildir account settings
  convertMaildirAccount = name: account: {
    path = account.path;
    account_name = account.accountName;
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
  };

in
{
  options.programs.emma = {
    enable = mkEnableOption "emma, an email automation platform with LLM processing";

    package = mkPackageOption pkgs "emma" { };

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
        description = "Local Maildir accounts to process";
        example = literalExpression ''
          {
            thunderbird = {
              path = "~/.thunderbird/profile/ImapMail/server";
              accountName = "personal";
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
  };

  config = mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.configFile."emma/config.yaml".source =
      settingsFormat.generate "emma-config.yaml" finalSettings;
  };
}
