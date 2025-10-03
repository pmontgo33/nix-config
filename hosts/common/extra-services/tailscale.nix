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
  
  # Detect if running in LXC container
  detectIsLXC = builtins.pathExists "/run/systemd/container" && 
                (builtins.readFile "/run/systemd/container") == "lxc\n" ||
                builtins.pathExists "/proc/1/cgroup" && 
                builtins.match ".*lxc.*" (builtins.readFile "/proc/1/cgroup") != null;
in {
  options.extra-services.tailscale = {
    enable = mkEnableOption "enable tailscale config";
    lxc = mkOption {
      type = types.bool;
      default = detectIsLXC;
      description = "Enable LXC mode (userspace networking). Auto-detected by default.";
    };
  };
  
  
  config = mkIf cfg.enable {
    warnings = [ 
      "Tailscale detectIsLXC: ${builtins.toString detectIsLXC}"
      "Tailscale cfg.lxc: ${builtins.toString cfg.lxc}"
    ];
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
      # Only use userspace networking if LXC mode is enabled
      interfaceName = mkIf cfg.lxc "userspace-networking";
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