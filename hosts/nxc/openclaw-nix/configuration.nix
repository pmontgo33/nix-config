{ config, pkgs, modulesPath, inputs, outputs, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
    inputs.nix-openclaw.nixosModules.openclaw-gateway
  ];

  networking.hostName = "openclaw";
  networking.firewall.allowedTCPPorts = [ 8384 22000 18789 ];
  networking.firewall.allowedUDPPorts = [ 22000 21027 ];

  environment.systemPackages = with pkgs; [ 
    jq 
    just 
    python311
    python311Packages.requests
    python311Packages.pip
    python311Packages.pdfplumber
    python311Packages.pandas
    python311Packages.openpyxl
    pkgs-unstable.claude-code
  ];

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;
  extra-services.obsidian.enable = true;

  # OpenClaw justfile for common commands
  environment.etc."openclaw/justfile".source = ./justfile;

  # Enable fish with 'oc' alias
  programs.fish = {
    enable = true;
    shellAliases = {
      oc = "just -f /etc/openclaw/justfile";
    };
  };

  services.openssh.enable = true;

  # Syncthing (runs as uid 1000)
  services.syncthing = {
    enable = true;
    user = "openclaw";
    group = "users";
    dataDir = "/var/lib/syncthing";
    guiAddress = "0.0.0.0:8384";
    overrideDevices = false;
    overrideFolders = false;
    settings.gui = {
      user = "patrick";
      password = "$2b$05$HyI3HBR7.6RpSjKnXJVXgOVfq/Kvmc6sDOpnYJ8EbY5U199kmLKZG";
    };
  };

  sops.secrets = {
    openclaw-telegram-token.mode = "0444";
    openclaw-env.mode = "0444";
    forgejo-mcp-env.mode = "0444";
  };

  # Create data directory (uid 1000 = openclaw user)
  systemd.tmpfiles.rules = [
    "d /var/lib/openclaw 0755 1000 100 -"
    "d /var/lib/syncthing 0700 1000 100 -"
  ];

  # OpenClaw gateway service (replaces Podman container)
  services.openclaw-gateway = {
    enable = true;
    port = 18789;
    user = "openclaw";
    group = "users";
    createUser = true;
    stateDir = "/var/lib/openclaw";
    configPath = "/etc/openclaw/openclaw.json";

    environment = {
      TZ = "America/New_York";
      OPENCLAW_GATEWAY_BIND = "0.0.0.0";
    };

    # Pass SOPS secrets as environment files
    environmentFiles = [
      config.sops.secrets.openclaw-env.path
      config.sops.secrets.openclaw-telegram-token.path
      config.sops.secrets.forgejo-mcp-env.path
    ];

    # OpenClaw JSON config — from actual /var/lib/openclaw/openclaw.json
    config = {
      meta = {
        lastTouchedVersion = "2026.4.1";
        lastTouchedAt = "2026-04-05T13:42:36.046Z";
      };

      env = {
        vars = {
          SEARXNG_BASE_URL = "http://192.168.86.137:8080";
        };
      };

      auth = {
        profiles = {
          "opencode:default" = { provider = "opencode"; mode = "api_key"; };
          "openrouter:default" = { provider = "openrouter"; mode = "api_key"; };
          "anthropic:default" = { provider = "anthropic"; mode = "token"; };
          "minimax-portal:default" = { provider = "minimax-portal"; mode = "oauth"; };
        };
        order = {
          claude = [ "anthropic" "opencode" ];
          sonnet = [ "anthropic" "opencode" ];
          opus = [ "anthropic" "opencode" ];
          haiku = [ "anthropic" "opencode" ];
        };
      };

      models = {
        providers = {
          minimax-portal = {
            baseUrl = "https://api.minimax.io/anthropic";
            apiKey = "minimax-oauth";
            api = "anthropic-messages";
            models = [
              { id = "MiniMax-M2.5"; name = "MiniMax M2.5"; reasoning = false; input = [ "text" ]; cost = { input = 0; output = 0; cacheRead = 0; cacheWrite = 0; }; contextWindow = 200000; maxTokens = 8192; }
              { id = "MiniMax-M2.5-highspeed"; name = "MiniMax M2.5 Highspeed"; reasoning = true; input = [ "text" ]; cost = { input = 0; output = 0; cacheRead = 0; cacheWrite = 0; }; contextWindow = 200000; maxTokens = 8192; }
              { id = "MiniMax-M2.5-Lightning"; name = "MiniMax M2.5 Lightning"; reasoning = true; input = [ "text" ]; cost = { input = 0; output = 0; cacheRead = 0; cacheWrite = 0; }; contextWindow = 200000; maxTokens = 8192; }
            ];
          };
        };
      };

      agents.defaults = {
        model = {
          primary = "minimax-portal/MiniMax-M2.7";
          fallbacks = [
            "opencode-go/minimax-m2.7"
            "opencode-go/kimi-k2.5"
            "anthropic/claude-haiku-4-5"
          ];
        };
        models = {
          "opencode-go/kimi-k2.5".alias = "Kimi";
          "opencode-go/minimax-m2.7".alias = "m2.7-go";
          "opencode-go/minimax-m2.5".alias = "m2.5-go";
          "opencode-go/glm-5".alias = "GLM-5 (Go)";
          "anthropic/claude-haiku-4-5".alias = "Haiku";
          "anthropic/claude-sonnet-4-6".alias = "Sonnet";
          "anthropic/claude-opus-4-6".alias = "Opus";
          "minimax-portal/MiniMax-M2.7".alias = "m2.7";
          "minimax-portal/MiniMax-M2.5".alias = "m2.5";
          "ollama/deepseek-coder:6.7b".alias = "DeepSeek Coder 6.7B";
          "ollama/qwen2.5:7b".alias = "Qwen 2.5 7B";
          "ollama/mistral:latest".alias = "Mistral Latest";
          "ollama/qwen2.5:14b".alias = "Qwen 2.5 14B";
          "ollama/llama3.1:8b".alias = "Llama 3.1 8B";
          "minimax-portal/MiniMax-M2.5-highspeed".alias = "minimax-m2.5-highspeed";
          "minimax-portal/MiniMax-M2.5-Lightning".alias = "minimax-m2.5-lightning";
        };
        workspace = "/home/node/.openclaw/workspace";
        contextPruning = {
          mode = "cache-ttl";
          ttl = "4h";
          keepLastAssistants = 3;
          softTrimRatio = 0.5;
          hardClearRatio = 0.8;
          minPrunableToolChars = 1000;
          softTrim = { maxChars = 15000; headChars = 2000; tailChars = 3000; };
          hardClear = {
            enabled = true;
            placeholder = "[Earlier context cleared to save tokens]";
          };
        };
        compaction = {
          mode = "safeguard";
          reserveTokens = 500;
          keepRecentTokens = 2000;
          reserveTokensFloor = 300;
          maxHistoryShare = 0.65;
          memoryFlush = {
            enabled = true;
            softThresholdTokens = 8000;
            prompt = "Summarize the key facts and decisions from this conversation for future reference.";
            systemPrompt = "You are a summarization expert. Extract essential information, decisions, and context. Be concise.";
          };
          recentTurnsPreserve = 5;
        };
        heartbeat.every = "1h";
        subagents.model = {
          primary = "minimax-portal/MiniMax-M2.7";
          fallbacks = [
            "opencode-go/kimi-k2.5"
            "minimax-portal/MiniMax-M2.5"
            "anthropic/claude-haiku-4-5"
          ];
        };
      };

      tools = {
        profile = "full";
        exec = {
          security = "full";
          ask = "off";
        };
        web.search.provider = "searxng";
      };

      commands = {
        native = "auto";
        nativeSkills = "auto";
        restart = true;
        ownerDisplay = "raw";
      };

      session.dmScope = "per-channel-peer";

      hooks = {
        enabled = true;
        path = "/hooks";
        token = "\${OPENCLAW_HOOKS_TOKEN}";
        defaultSessionKey = "hook:ingress";
        allowRequestSessionKey = false;
        allowedSessionKeyPrefixes = [ "hook:" ];
        allowedAgentIds = [ "*" ];
        mappings = [
          {
            id = "ha-alert";
            match = { path = "ha-alert"; };
            action = "agent";
            name = "HomeAssistant";
            messageTemplate = "HomeAssistant event: {{ payload.type }} | {{ payload.entity }} | {{ payload.state }} | {{ payload.automation_id }}";
            deliver = true;
            channel = "telegram";
            to = "748642877";
            wakeMode = "now";
          }
        ];
      };

      channels = {
        telegram = {
          enabled = true;
          dmPolicy = "allowlist";
          botToken = "\${OPENCLAW_TELEGRAM_TOKEN}";  # from SOPS secret
          allowFrom = [ "748642877" ];
          groupPolicy = "allowlist";
          streaming = "partial";
          execApprovals.enabled = false;
        };
        discord = {
          enabled = true;
          groupPolicy = "allowlist";
          streaming = "off";
        };
      };

      gateway = {
        port = 18789;
        mode = "local";
        bind = "lan";
        controlUi = {
          allowedOrigins = [
            "http://192.168.86.136:18789"
            "https://192.168.86.136:18789"
            "http://192.168.86.136"
            "https://192.168.86.136"
            "http://localhost:18789"
            "https://localhost:18789"
            "http://127.0.0.1:18789"
            "https://127.0.0.1:18789"
            "https://openclaw.montycasa.net"
            "https://openclaw.montycasa.net:443"
            "null"
            "http://100.111.130.47:18789"
            "https://100.111.130.47:18789"
            "http://100.111.130.47"
            "https://100.111.130.47"
          ];
        };
        auth = {
          mode = "token";
          token = "\${OPENCLAW_GATEWAY_TOKEN}";  # from SOPS secret
        };
        tailscale = {
          mode = "off";
          resetOnExit = false;
        };
        remote = {
          url = "ws://127.0.0.1:18789";
          token = "\${OPENCLAW_GATEWAY_TOKEN}";  # from SOPS secret
        };
      };

      skills = {
        install.nodeManager = "npm";
        entries = {
          "apple-reminders".enabled = false;
          "bear-notes".enabled = false;
          "blucli".enabled = false;
          "bluebubbles".enabled = false;
        };
      };

      plugins = {
        slots.memory = "memory-lancedb";
        entries = {
          telegram.enabled = true;
          discord.enabled = true;
          minimax.enabled = true;
          "memory-lancedb" = {
            enabled = true;
            config = {
              embedding = {
                model = "openai/text-embedding-3-small";
                apiKey = "\${OPENROUTER_API_KEY}";  # from SOPS
                baseUrl = "https://openrouter.ai/api/v1";
                dimensions = 1536;
              };
              autoCapture = true;
              autoRecall = true;
            };
          };
          "memory-core".enabled = false;
          searxng = {
            enabled = true;
            config.webSearch.baseUrl = "http://192.168.86.137:8080";
          };
        };
      };
    };

    restart = "always";
    restartSec = 10;
  };

  system.stateVersion = "25.11";
}
