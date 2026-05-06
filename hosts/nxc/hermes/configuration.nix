{ config, pkgs, modulesPath, inputs, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
  agentmail = pkgs.python312Packages.buildPythonPackage rec {
    pname = "agentmail";
    version = "0.5.0";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/d9/f0/4c7dbbd1db1b820eb1206636b6be146655cff497a6d7739669432d8f0553/agentmail-0.5.0-py3-none-any.whl";
      hash = "sha256-ALyfhuTG/i9aMibBZg3boq3LgGPIBUD+Pp5WT+KFsjM=";
    };
    format = "wheel";
    propagatedBuildInputs = with pkgs.python312Packages; [
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

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;

  services.openssh.enable = true;

  environment.systemPackages = with pkgs; [
    pkgs-unstable.claude-code
    hermes-agent
    tmux
    pkgs.jq

    (pkgs.python312.withPackages (ps: with ps; [
      ps.pandas
      ps.pdfplumber
      ps.openpyxl
      agentmail
    ]))
  ];

  programs.fish = {
    enable = true;
    interactiveShellInit = ''
      function hermes
        HERMES_HOME=/var/lib/hermes/.hermes /run/current-system/sw/bin/hermes $argv
      end
    '';
  };

  # Reuses openclaw-env secret — contains shared API keys (Anthropic, OpenRouter,
  # MiniMax, etc.). Telegram token must be a NEW bot separate from openclaw's to
  # avoid double-responses; add TELEGRAM_BOT_TOKEN to this secret for hermes's bot.
  sops.secrets."openclaw-env".mode = "0444";

  services.hermes-agent = {
    enable = true;
    extraPythonPackages = with pkgs.python312Packages; [ fastapi uvicorn ptyprocess ];
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
    };

    settings = {
      model = "minimax/MiniMax-M2.7";

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
        { provider = "google"; model = "gemini-flash-latest"; }
        { provider = "openrouter"; model = "moonshot/kimi-k2"; }
        { provider = "anthropic"; model = "claude-haiku-4-5-20251001"; }
      ];

      auxiliary = {
        provider = "minimax";
        model = "MiniMax-M2.7";
      };

      homeassistant = {
        enabled = true;
        url = "http://192.168.86.100:8123";
        watch_entities = [ "binary_sensor.away_mode" ];
        watch_all = false;
        cooldown_seconds = 10;
      };

      toolsets = [ "hermes-cli" "files" "web" "computer" ];

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
      };

      # Telegram — requires a NEW bot token separate from openclaw's.
      # Set TELEGRAM_BOT_TOKEN in openclaw-env to hermes's bot token.
      # Allow-list mirrors openclaw (user 748642877 = Monty).
      telegram = {
        reactions = false;
      };

      platforms = {
        webhook = {
          enabled = true;
          extra = {
            host = "0.0.0.0";
            port = 8644;
            secret = "skYQYIzpOKXwVH8R726AdxhZbZPNctlKimtpmMQvj7HFCoh9";
          };
        };
      };

      terminal = {
        cwd = "/var/lib/hermes/workspace";
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
  };

  users.users.hermes.linger = true;

  # Profile directories for named subagents (Rocket, Friday)
  systemd.services.hermes-profiles = {
    wantedBy = [ "multi-user.target" ];
    script = ''
      for profile in rocket friday; do
        for subdir in cron sessions logs logs/curator memories; do
          mkdir -p "/var/lib/hermes/.hermes/profiles/$profile/$subdir"
          chown -R hermes:users "/var/lib/hermes/.hermes/profiles/$profile"
          chmod 2775 "/var/lib/hermes/.hermes/profiles/$profile/$subdir"
        done
      done
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
