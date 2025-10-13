{ lib, config, ... }:

{
  options.extra-services.local-proxy = {
    enable = lib.mkEnableOption "enable local-proxy for host";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "${config.networking.hostName}.local";
      description = "Domain name for this service";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 80;
      description = "Port to proxy to";
    };
  };
}
