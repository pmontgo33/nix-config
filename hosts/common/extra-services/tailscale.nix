  /*
  For installation on LXC, add these lines to /etc/pve/lxc/ID.conf:
  lxc.cgroup2.devices.allow: c 10:200 rwm
  lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file

  For LXC, run:
  tailscale up --ssh --accept-dns=false
  For other, run:
  tailscale up --ssh

  */

{config, lib, ...}:
with lib; let
  cfg = config.extra-services.tailscale;
in {
  options.extra-services.tailscale.enable = mkEnableOption "enable tailscale config";
  
  config = mkIf cfg.enable {
    # sops secrets configuration
    sops = {
      # defaultSopsFile = ../../../secrets/secrets.yaml;
      secrets = {
        "tailscale_auth_key" = {};
      };
    };
    
    services.tailscale = {
      enable = true;
      openFirewall = true;
      interfaceName = "userspace-networking";
      authKeyFile = config.sops.secrets.tailscale_auth_key.path;
      useRoutingFeatures = "client";
      extraUpFlags = [
        "--force-reauth"
        "--reset"
        "--ssh"
        "--accept-routes"
        "--accept-dns=false"
      ];
    };
    

    networking.nameservers = [ "100.100.100.100" "192.168.86.1" "1.1.1.1" ];
    networking.search = [ "skink-galaxy.ts.net" ];
    
  };
}