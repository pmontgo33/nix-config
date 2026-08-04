/*
  Hermes-Relay Service Module

  Runs the Hermes-Relay WSS relay server (port 8767 by default) as a
  system-level systemd service for the hermes user. The plugin layer
  (`~/.hermes/plugins/hermes-relay/`) is wired separately by
  `hosts/nxc/hermes/configuration.nix` via the `extraPlugins` mechanism.

  Surface separation:
    - Vanilla Hermes dashboard/chat (`:9119`) and API server (`:8642`)
      remain unchanged.
    - This module adds the optional relay listener used by the
      Hermes-Relay Android app and CLI for terminal, bridge phone
      control, media routes, and relay sessions.

  Network model:
    - Listens on `0.0.0.0:8767` so the local hermes-agent can reach it
      on loopback.
    - Firewall exposes `8767/tcp` ONLY on `tailscale0` — never on the
      LAN or WAN interface. Phone pairing happens over the Tailnet.
    - For public exposure, layer TLS / reverse proxy in front and switch
      to `wss://` upstream; this module is the trusted-Tailnet default.
*/
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.extra-services.hermes-relay;

  # Python interpreter for the relay service. The hermes-relay package
  # itself is the top-level overlay attr (a buildPythonPackage derivation);
  # we wrap it via withPackages so transitive runtime deps (aiohttp,
  # segno, python-dotenv, ...) are guaranteed on PYTHONPATH.
  #
  # python-dotenv is added explicitly: the relay wheel's `_env_bootstrap.py`
  # loads HERMES_HOME/.env silently and falls back to no-op if the module
  # is unavailable. Without it, paired-device tokens, API keys, and other
  # settings stay empty at runtime.
  relayPython = pkgs.python312.withPackages (ps: [
    cfg.package
    ps.python-dotenv
    pkgs.tmux
    pkgs.bash
    pkgs.coreutils
  ]);
in
{
  options.extra-services.hermes-relay = {
    enable = mkEnableOption "Hermes-Relay WSS relay server (Android/desktop power tools)";

    package = mkOption {
      type = types.package;
      default = pkgs.hermes-relay or (throw "hermes-relay package not on pkgs; pass services.hermes-relay.package explicitly or import packages/hermes-relay.nix via overlays.");
      description = "Hermes-Relay Python wheel derivation.";
    };

    user = mkOption {
      type = types.str;
      default = "hermes";
      description = "User the relay server runs as.";
    };

    group = mkOption {
      type = types.str;
      default = "users";
      description = "Primary group the relay server runs as.";
    };

    port = mkOption {
      type = types.port;
      default = 8767;
      description = "TCP port the relay server listens on.";
    };

    bind = mkOption {
      type = types.str;
      default = "0.0.0.0";
      description = "Address the relay server binds to.";
    };

    stateDir = mkOption {
      type = types.path;
      default = "/var/lib/hermes/.hermes-relay";
      description = "Persistent directory for relay sessions, QR signing keys, and Tailscale discovery state.";
    };

    sessionsFile = mkOption {
      type = types.nullOr types.path;
      default = "/var/lib/hermes/.hermes-relay/sessions.json";
      description = "Path where paired-device session JSON is persisted. Set to null to let the relay use its own default.";
    };

    openTailscaleFirewall = mkOption {
      type = types.bool;
      default = true;
      description = "Whether to expose port 8767 on the Tailscale interface only.";
    };

    webapiUrl = mkOption {
      type = types.str;
      default = "http://127.0.0.1:8642";
      description = "URL of the local Hermes API server the relay forwards dashboard commands to.";
    };

    logLevel = mkOption {
      type = types.enum [ "DEBUG" "INFO" "WARNING" "ERROR" ];
      default = "INFO";
      description = "Relay server log verbosity.";
    };

    startOnBoot = mkOption {
      type = types.bool;
      default = true;
      description = "Whether to start the relay service automatically.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.user == "hermes";
        message = "Hermes-Relay must run as the hermes user so it can read ~/.hermes/config.yaml and ~/.hermes/.env.";
      }
    ];

    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0750 ${cfg.user} ${cfg.group} -"
    ];

    systemd.services.hermes-relay = {
      description = "Hermes-Relay WSS server (Android/desktop power tools)";
      wantedBy = mkIf cfg.startOnBoot [ "multi-user.target" ];
      after = [ "network-online.target" "hermes-agent.service" ];
      wants = [ "network-online.target" ];
      # Do not require hermes-agent.service: if the API server is down the
      # relay should still bind so the phone can pair and surface diagnostics.

      environment = {
        HOME = "/var/lib/hermes";
        HERMES_HOME = "/var/lib/hermes/.hermes";
        HERMES_RELAY_HOME = cfg.stateDir;
        RELAY_HOST = cfg.bind;
        RELAY_PORT = toString cfg.port;
        RELAY_WEBAPI_URL = cfg.webapiUrl;
        RELAY_HERMES_CONFIG = "/var/lib/hermes/.hermes/config.yaml";
        RELAY_LOG_LEVEL = cfg.logLevel;
      } // optionalAttrs (cfg.sessionsFile != null) {
        RELAY_SESSIONS_FILE = cfg.sessionsFile;
      };

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        Group = cfg.group;
        ExecStart = "${relayPython}/bin/python -m plugin.relay --no-ssl";
        # ExecSearchPath (NixOS option: serviceConfig.ExecSearchPath) sets
        # the PATH used by ExecStart and any spawned children. The
        # relayPython closure already puts the venv's bin first; adding
        # pkgs.tmux, pkgs.bash, pkgs.coreutils ensures the persistent-
        # terminal session spawns tmux directly without relying on
        # environment.systemPackages being inherited (it is not, by
        # default, in systemd unit PATHs).
        ExecSearchPath = "${relayPython}/bin:${pkgs.tmux}/bin:${pkgs.bash}/bin:${pkgs.coreutils}/bin";
        Restart = "on-failure";
        RestartSec = 5;
        # Allow the relay to read the hermes config + .env, persist
        # sessions, and write tmux/profile files under HERMES_HOME that
        # the persistent-terminal + voice-lab features rely on. The
        # state dir is owned by the hermes user (tmpfiles rule above).
        ProtectSystem = "strict";
        ReadWritePaths = [
          cfg.stateDir
          "/var/lib/hermes/.hermes"
        ];
        ProtectHome = false;
        NoNewPrivileges = true;
        PrivateTmp = true;
        # Loosen for relay voice audio / network reachability to hermes-agent.
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
      };
    };

    networking.firewall = mkIf cfg.openTailscaleFirewall {
      interfaces.tailscale0.allowedTCPPorts = [ cfg.port ];
    };
  };
}