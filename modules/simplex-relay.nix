/*
  SimpleX Relay Server Module

  This module runs a SimpleX messaging relay server (SMP server) using Docker.

  Features:
  - Runs official SimpleX Docker container
  - Automatic certificate generation
  - Configurable host and ports
  - Firewall configuration
  - Proper state management

  Usage:
    extra-services.simplex-relay = {
      enable = true;
      host = "smp.example.com";
      port = 5223;
      openFirewall = true;
    };
*/

{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.extra-services.simplex-relay;

in {
  options.extra-services.simplex-relay = {
    enable = mkEnableOption "SimpleX messaging relay server";

    image = mkOption {
      type = types.str;
      default = "simplexchat/smp-server:latest";
      description = "Docker image to use for SimpleX SMP server";
    };

    host = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "smp.example.com";
      description = ''
        Public hostname for the SMP server. This will be used in the server address
        that clients connect to. If not set, the server will use its IP address.
      '';
    };

    port = mkOption {
      type = types.port;
      default = 5223;
      description = "Port for the SMP protocol";
    };

    enableWebsockets = mkOption {
      type = types.bool;
      default = false;
      description = "Enable WebSocket support for web clients";
    };

    websocketPort = mkOption {
      type = types.port;
      default = 5224;
      description = "Port for WebSocket connections (only used if enableWebsockets is true)";
    };

    stateDir = mkOption {
      type = types.path;
      default = "/var/lib/simplex";
      description = "Directory for SimpleX server state and data";
    };

    openFirewall = mkOption {
      type = types.bool;
      default = true;
      description = "Whether to open firewall ports for the SMP server";
    };

    autoStart = mkOption {
      type = types.bool;
      default = true;
      description = "Whether to start the container automatically";
    };

    extraConfig = mkOption {
      type = types.lines;
      default = "";
      example = ''
        [TRANSPORT]
        tcp_timeout: 120
      '';
      description = "Extra configuration to append to smp-server.ini";
    };
  };

  config = mkIf cfg.enable {
    # Enable Podman for containers
    virtualisation.podman = {
      enable = true;
      dockerCompat = true;
      defaultNetwork.settings.dns_enabled = true;
    };

    # Ensure the state directory exists
    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0755 root root -"
      "d ${cfg.stateDir}/config 0755 root root -"
      "d ${cfg.stateDir}/logs 0755 root root -"
    ];

    # Initialize the SimpleX server with proper hostname
    systemd.services.simplex-relay-init = {
      description = "Initialize SimpleX Relay Server";
      wantedBy = [ "simplex-relay.service" ];
      before = [ "simplex-relay.service" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };

      script = ''
        # Only initialize if database doesn't exist
        if [ ! -f ${cfg.stateDir}/config/smp-server.db ]; then
          echo "Initializing SimpleX server..."
          ${pkgs.podman}/bin/podman run --rm \
            -v ${cfg.stateDir}/config:/etc/opt/simplex:z \
            -v ${cfg.stateDir}/logs:/var/opt/simplex:z \
            ${cfg.image} init -l --store-log -y \
            ${optionalString (cfg.host != null) "-n ${cfg.host}"} \
            -p ${toString cfg.port}
          echo "SimpleX server initialized"
        else
          echo "SimpleX server already initialized"
        fi
      '';
    };

    # OCI container for SimpleX relay
    virtualisation.oci-containers = {
      backend = "podman";
      containers.simplex-relay = {
        image = cfg.image;
        autoStart = cfg.autoStart;

        ports = [
          "${toString cfg.port}:5223"
        ] ++ optional cfg.enableWebsockets "${toString cfg.websocketPort}:5224";

        volumes = [
          "${cfg.stateDir}/config:/etc/opt/simplex:z"
          "${cfg.stateDir}/logs:/var/opt/simplex:z"
        ];

        # No ADDR needed - it will read from config file
        cmd = [ "start" ];
      };
    };

    # Open firewall ports if requested
    networking.firewall = mkIf cfg.openFirewall {
      allowedTCPPorts = [ cfg.port ]
        ++ optional cfg.enableWebsockets cfg.websocketPort;
    };
  };
}
