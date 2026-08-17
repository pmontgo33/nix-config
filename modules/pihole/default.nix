{ config, lib, ... }:

let
  cfg = config.services.pihole-native;
in
{
  options.services.pihole-native = {
    enable = lib.mkEnableOption "native Pi-hole FTL and Web services";

    interface = lib.mkOption {
      type = lib.types.str;
      default = "eth0";
      description = "Network interface on which Pi-hole accepts DNS queries.";
    };

    listeningMode = lib.mkOption {
      type = lib.types.enum [
        "LOCAL"
        "SINGLE"
        "BIND"
        "ALL"
        "NONE"
      ];
      default = "BIND";
      description = "Pi-hole FTL DNS listening mode.";
    };

    upstreams = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Upstream DNS servers used by Pi-hole FTL; required per host.";
    };

    webHostName = lib.mkOption {
      type = lib.types.str;
      default = "pi.hole";
      description = "Pi-hole Web virtual host name.";
    };

    webPort = lib.mkOption {
      type = lib.types.port;
      default = 8080;
      description = "TCP port for the Pi-hole Web interface and API.";
    };

    webListenAddress = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address for the unauthenticated experimental Web/API listener.";
    };

    openFirewallDNS = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Open the host firewall for DNS.";
    };

    privacyLevel = lib.mkOption {
      type = lib.types.ints.between 0 3;
      default = 0;
      description = "Pi-hole FTL statistics privacy level.";
    };

    stateDirectory = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/pihole";
      description = "Persistent Pi-hole state directory.";
    };

    logDirectory = lib.mkOption {
      type = lib.types.str;
      default = "/var/log/pihole";
      description = "Persistent Pi-hole log directory.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.upstreams != [ ];
        message = "services.pihole-native.upstreams must be set explicitly for each Pi-hole host.";
      }
      {
        assertion = cfg.webListenAddress == "127.0.0.1";
        message = "services.pihole-native.webListenAddress must remain loopback until API authentication is wired through SOPS.";
      }
    ];

    services.pihole-ftl = {
      enable = true;
      privacyLevel = cfg.privacyLevel;
      openFirewallDNS = cfg.openFirewallDNS;
      openFirewallWebserver = false;
      stateDirectory = cfg.stateDirectory;
      logDirectory = cfg.logDirectory;
      settings = {
        dns = {
          inherit (cfg) interface listeningMode upstreams;
        };
        webserver.api = {
          # Required by the native list setup service. Keep this ephemeral
          # credential separate from the eventual reconciler credential.
          cli_pw = true;
          app_sudo = false;
        };
      };
    };

    services.pihole-web = {
      enable = true;
      hostName = cfg.webHostName;
      ports = [ "${cfg.webListenAddress}:${toString cfg.webPort}" ];
    };
  };
}
