{ config, pkgs, modulesPath, inputs, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
    inputs.nix-hermes-agent.nixosModules.default
  ];

  networking.hostName = "hermes";
  networking.firewall.allowedTCPPorts = [ 8642 ];

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;

  services.openssh.enable = true;

  environment.systemPackages = [ pkgs.hermes-agent ];

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
      # Primary model: MiniMax M2.7 via minimax-portal (Anthropic-compatible API).
      # Hermes uses OpenAI-compatible providers via custom_providers.
      # Fallback chain mirrors openclaw: openrouter kimi-k2.5, then claude-haiku.
      model = "minimax-portal/MiniMax-M2.7";

      fallback_providers = [
        { provider = "openrouter"; model = "moonshot/kimi-k2"; }
        { provider = "anthropic"; model = "claude-haiku-4-5-20251001"; }
      ];

      # MiniMax via their Anthropic-compatible portal
      custom_providers = [
        {
          name = "minimax-portal";
          base_url = "https://api.minimax.io/anthropic";
          api_key_env = "MINIMAX_API_KEY";
          models = [
            { id = "MiniMax-M2.7"; context_length = 200000; }
            { id = "MiniMax-M2.5"; context_length = 200000; }
            { id = "MiniMax-M2.5-highspeed"; context_length = 200000; }
            { id = "MiniMax-M2.5-Lightning"; context_length = 200000; }
          ];
        }
      ];

      toolsets = [ "hermes-cli" "files" "web" "computer" ];

      agent = {
        max_turns = 90;
        gateway_timeout = 1800;
      };

      compression = {
        enabled = true;
        threshold = 0.50;
        target_ratio = 0.20;
        protect_last_n = 20;
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
    };
  };

  # Inject allowlist into the systemd environment so hermes's os.getenv()
  # check sees it at startup (the module writes these to .env but the gateway
  # allowlist check uses os.getenv, not hermes's own .env loader).
  systemd.services.hermes-agent.environment = {
    TELEGRAM_ALLOWED_USERS = "748642877";
  };

  environment.systemPackages = with pkgs; [
    pkgs-unstable.claude-code
  ];

  system.stateVersion = "25.11";
}
