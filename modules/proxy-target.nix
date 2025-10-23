{ config, lib, ... }:

with lib;

let
  cfg = config.extra-services.proxy-target;
in
{
  options.extra-services.proxy-target = {
    enable = mkEnableOption "proxy target support";

    hostAddress = mkOption {
      type = types.str;
      description = "IP address or hostname of this host from the proxy's perspective";
      example = "192.168.1.10";
    };

    services = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          subdomain = mkOption {
            type = types.str;
            description = "Subdomain for this service (or full domain if it contains dots)";
            example = "api";
          };

          port = mkOption {
            type = types.port;
            description = "Local port the service listens on";
            example = 8080;
          };

          protocol = mkOption {
            type = types.enum [ "http" "https" "tcp" "udp" ];
            default = "http";
            description = "Protocol to proxy";
          };

          tags = mkOption {
            type = types.listOf types.str;
            default = [];
            description = "Tags identifying this service for proxy routing";
            example = [ "web" "frontend" "public" ];
          };

          extraCaddyConfig = mkOption {
            type = types.lines;
            default = "";
            description = "Extra Caddy configuration for this service";
            example = ''
              header {
                X-Custom-Header "value"
              }
            '';
          };
        };
      });
      default = {};
      description = "Services to expose through proxies";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.hostAddress != "";
        message = "extra-services.proxy-target.hostAddress must be set";
      }
    ];
  };
}