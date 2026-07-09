{ config, pkgs, lib, modulesPath, inputs, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
  python312 = pkgs.python312.override {
    packageOverrides = _self: super: {
      sse-starlette = super.sse-starlette.overridePythonAttrs (_: { dontCheckRuntimeDeps = true; });
      pymupdf = super.pymupdf.overridePythonAttrs (_: { doCheck = false; });
      pdfplumber = super.pdfplumber.overridePythonAttrs (_: { doCheck = false; });
    };
  };
  hermesPython = lib.getOutput "out" (python312.withPackages (ps: [
    ps.pandas
    ps.pdfplumber
    ps.openpyxl
    ps.reportlab
    ps.fastapi
    ps.uvicorn
    ps.ptyprocess
    ps.python-telegram-bot
    ps.mcp
    ps.icalendar
    ps.pymupdf
    ps.pytesseract
    ps.pillow
    ps.darkdetect
    agentmail
  ]));
  agentmail = python312.pkgs.buildPythonPackage rec {
    pname = "agentmail";
    version = "0.5.0";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/d9/f0/4c7dbbd1db1b820eb1206636b6be146655cff497a6d7739669432d8f0553/agentmail-0.5.0-py3-none-any.whl";
      hash = "sha256-ALyfhuTG/i9aMibBZg3boq3LgGPIBUD+Pp5WT+KFsjM=";
    };
    format = "wheel";
    propagatedBuildInputs = with python312.pkgs; [
      httpx pydantic websockets
    ];
    doCheck = false;
  };
in

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
    inputs.nix-hermes-agent.nixosModules.default
  ];

  nixpkgs.overlays = [
    (final: prev: {
      python312 = prev.python312.override {
        packageOverrides = _self: super:
          builtins.mapAttrs (_name: val:
            if builtins.isAttrs val && val ? overrideAttrs
            then val.overrideAttrs (_: { doCheck = false; doInstallCheck = false; })
            else val
          ) super;
      };
    })
  ];

  networking.hostName = "hermes";
  networking.firewall.allowedTCPPorts = [ 8642 8644 9119 ];

  # python3.12 doc build broken in nixpkgs 26.05 (upstream issue #529084)
  documentation.man.enable = false;
  documentation.doc.enable = false;

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;
  extra-services.obsidian-headless = {
    enable = true;
    vaults.MontyVault.path = "/var/lib/hermes/vault/MontyVault";
  };

  services.openssh.enable = true;

  # SSH client: trust Home Assistant's host key declaratively
  programs.ssh.knownHosts = {
    "home-assistant" = {
      hostNames = [ "192.168.86.100" "homeassistant" "ha" ];
      publicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMzBHEg142uYU3qgiuUa3afGEVcI9JPe5a4aX4gnyHJ1";
    };
  };

  environment.systemPackages = with pkgs; [
    pkgs-unstable.claude-code
    tmux
    pkgs.jq
    pkgs.tesseract
    hermesPython
  ];

  programs.fish.enable = true;

  # Reuses openclaw-env secret — contains shared API keys (Anthropic, OpenRouter,
  # MiniMax, etc.). Telegram token must be a NEW bot separate from openclaw's to
  # avoid double-responses; add TELEGRAM_BOT_TOKEN to this secret for hermes's bot.
  sops.secrets."openclaw-env".mode = "0444";
  sops.secrets."hermes-webhook".mode = "0444";

  services.hermes-agent = {
    enable = true;
    addToSystemPackages = true;
    extraDependencyGroups = [ "messaging" "anthropic" "voice" ];

    # mcpServers.forgejo = {
    #   url = "http://192.168.86.120:8080/sse";
    # };
    user = "hermes";
    group = "users";
    createUser = true;
    stateDir = "/var/lib/hermes";
    environmentFiles = [ config.sops.secrets.openclaw-env.path ];
    environment = {
      TZ = "America/New_York";
      API_SERVER_ENABLED = "true";
      API_SERVER_HOST = "0.0.0.0";
      API_SERVER_PORT = "8642";
      SEARXNG_BASE_URL = "http://192.168.86.137:8080";
      TELEGRAM_ALLOWED_USERS = "748642877";
      TELEGRAM_HOME_CHANNEL = "748642877";
    };

    settings = {
      model = {
        default = "MiniMax-M3";
        provider = "minimax";
      };

      custom_providers = [
        {
          name = "google";
          base_url = "https://generativelanguage.googleapis.com/v1beta";
          api_key_env = "GEMINI_API_KEY";
          models = [
            { id = "gemini-flash-latest"; context_length = 1000000; }
            { id = "gemini-3-flash-preview"; context_length = 1000000; }
          ];
        }
      ];

      fallback_providers = [
        { provider = "minimax"; model = "MiniMax-M2.7"; }
        { provider = "google"; model = "gemini-flash-latest"; }
        { provider = "opencode-go"; model = "minimax-m2.7"; }
      ];

      # Mixture of Agents preset: DeepSeek v4 Flash (opencode-go) aggregates
      # three references — Xiaomi MiMo for reasoning-style analysis,
      # MiniMax-M3 (minimax) for synthesis depth, Qwen 3.6 Plus for breadth.
      # Use via `/model hydra --provider moa` or one-shot `/moa <prompt>`.
      # Set per-preset `enabled = false` to fall back to the aggregator
      # acting alone.
      moa = {
        default_preset = "hydra";
        presets.hydra = {
          reference_models = [
            { provider = "opencode-go"; model = "mimo-v2.5"; }
            { provider = "minimax"; model = "MiniMax-M3"; }
            { provider = "opencode-go"; model = "qwen3.6-plus"; }
          ];
          aggregator = {
            provider = "opencode-go";
            model = "deepseek-v4-flash";
          };
          max_tokens = 4096;
          reference_max_tokens = 600;  # cap advisor output for snappier turns
          enabled = true;
        };
      };

      auxiliary = {
        provider = "minimax";
        model = "MiniMax-M2.7";
        vision = {
          provider = "opencode-go";
          model = "mimo-v2.5";
        };
      };

      approvals = {
        mode = "smart";
      };

      toolsets = [ "hermes-cli" "files" "web" "computer" "memory" ];

      agent = {
        max_turns = 90;
        gateway_timeout = 1800;
      };

      compression = {
        enabled = true;
        threshold = 0.85;
        target_ratio = 0.20;
        protect_last_n = 120;
      };

      memory = {
        memory_enabled = true;
        user_profile_enabled = true;
        # Holographic — bundled first-party memory provider (SQLite + FTS5 +
        # HRR compositional retrieval). Pure local, no network, no embeddings.
        # Coexists additively with built-in MEMORY.md/USER.md.
        provider = "holographic";
      };

      # Real-time token streaming over Telegram (editMessageText / sendMessageDraft)
      streaming = {
        enabled = true;
        transport = "auto";
        edit_interval = 0.8;
        buffer_threshold = 24;
        fresh_final_after_seconds = 60;
      };

      # Voice transcription (STT) — local faster-whisper, no API key needed
      stt = {
        enabled = true;
        provider = "local";
        local = {
          model = "base";
        };
      };

      # Telegram — requires a NEW bot token separate from openclaw's.
      # Set TELEGRAM_BOT_TOKEN in openclaw-env to hermes's bot token.
      # Allow-list mirrors openclaw (user 748642877 = Monty).
      telegram = {
        reactions = false;
      };

      platforms = {
        homeassistant = {
          enabled = true;
          extra = {
            url = "http://192.168.86.100:8123";
            watch_entities = [ "binary_sensor.away_mode" "sensor.pat_phone_next_alarm" ];
            watch_all = false;
            cooldown_seconds = 10;
          };
        };

        webhook = {
          enabled = true;
          extra = {
            host = "0.0.0.0";
            port = 8644;
            secret = config.sops.secrets."hermes-webhook".path;
            routes = {
              "ha-alert" = {
                secret = config.sops.secrets."hermes-webhook".path;
              };
            };
          };
        };
      };

      terminal = {
        cwd = "/var/lib/hermes/workspace";
      };

      checkpoints = {
        enabled = true;
        auto_prune = true;
      };

    };

    # SOUL.md — injected as a workspace document at activation time
    documents."SOUL.md" = builtins.readFile ./documents/SOUL.md;
  };

  # Inject allowlist into the systemd environment so hermes's os.getenv()
  # check sees it at startup (the module writes these to .env but the gateway
  # allowlist check uses os.getenv, not hermes's own .env loader).
  systemd.services.hermes-agent.environment = {
    TELEGRAM_ALLOWED_USERS = "748642877";
    HERMES_MANAGED = "true";
    ANTHROPIC_TOKEN = "***";
  };

  # Fix file ownership after nix rebuilds. The activation script chowns
  # directories but not individual files — if anything runs as root and
  # touches a file under .hermes/ (e.g. cron/jobs.json during a service
  # restart race), it becomes root-owned and the gateway can't read it.
  # This self-heals on every service start.
  systemd.services.hermes-agent.postStart = ''
    find /var/lib/hermes/.hermes -maxdepth 3 \! -user hermes -exec chown hermes:users {} + 2>/dev/null || true
  '';

  users.users.hermes = {
    extraGroups = [ "obsidian-headless" ];
    linger = true;
  };
  users.users.root.linger = true;

  systemd.services.rocket-githook = {
    wantedBy = [ "multi-user.target" ];
    after = [ "hermes-agent.service" ];
    script = ''
      HOOK_DIR="/var/lib/hermes/.hermes/git/nix-config/.git/hooks"
      mkdir -p "$HOOK_DIR"
      cp ${./githooks/pre-push} "$HOOK_DIR/pre-push"
      chmod +x "$HOOK_DIR/pre-push"
      chown hermes:users "$HOOK_DIR/pre-push"
    '';
  };

  systemd.services.hermes-dashboard = {
    wantedBy = [ "multi-user.target" ];
    after = [ "hermes-agent.service" ];
    environment = {
      HERMES_HOME = "/var/lib/hermes/.hermes";
    };
    serviceConfig = {
      User = "hermes";
      Group = "users";
      ExecStart = "${pkgs.hermes-agent}/bin/hermes dashboard --host 0.0.0.0 --port 9119 --no-open --insecure";
      Restart = "on-failure";
    };
  };

  system.stateVersion = "25.11";
}
