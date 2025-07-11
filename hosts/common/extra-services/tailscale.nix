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
      defaultSopsFile = ../../../secrets/secrets.yaml;
      secrets = {
        "tailscale_auth_key" = {};
      };
    };

    services.tailscale = {
      enable = true;
  #    interfaceName = "userspace-networking";
      openFirewall = true;
      authKeyFile = config.sops.secrets.tailscale_auth_key.path;
      useRoutingFeatures = "both";
      extraUpFlags = [
        "--force-reauth"
        "--reset"
        "--ssh"
        "--accept-routes"
  #      "--accept-dns=false"
      ];
    };

  #  networking.nameservers = [ "100.100.100.100"];
    networking.nameservers = [ "100.100.100.100" "1.1.1.1" "1.0.0.1" ];
    networking.search = [ "skink-galaxy.ts.net" ];
    networking.firewall.allowedUDPPorts = [ 41641 ];

    # networking.localCommands = ''
    #   ip rule add to 192.168.86.0/24 priority 2500 lookup main
    # '';
  
  };

  
}
