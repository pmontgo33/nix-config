/*
 For installation on LXC, add these lines to /etc/pve/lxc/ID.conf:
 lxc.cgroup2.devices.allow: c 10:200 rwm
 lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
 For LXC, run:
 tailscale up --ssh --accept-dns=false
 For other, run:
 tailscale up --ssh
 */
{config, lib, pkgs, inputs, ...}:
with lib; let
  cfg = config.extra-services.tailscale;
in {
  options.extra-services.tailscale = {
    enable = mkEnableOption "enable tailscale config";
    userspace-networking = mkOption {
      type = types.bool;
      default = false;
      description = "Enable userspace networking";
    };
    lxc = mkOption {
      type = types.bool;
      default = false;
      description = "Enable LXC-specific fixes for local network routing";
    };
    tags = mkOption {
      type = types.listOf types.str;
      default = [];
      example = [ "tag:server" "tag:web" ];
      description = "Tailscale tags to apply to this node";
    };
    extraFlags = mkOption {
      type = types.listOf types.str;
      default = [];
      example = [ "--advertise-routes=192.168.1.0/24" "--netfilter-mode=off" ];
      description = "Additional flags to pass to tailscale up";
    };
  };
  
  imports = [
    ../secrets
  ];
  
  config = mkIf cfg.enable {
    sops = {
      secrets = {
        "tailscale_auth_key" = {};
      };
    };
    
    services.tailscale = {
      enable = true;
      package = inputs.nixpkgs-unstable.legacyPackages.${pkgs.system}.tailscale;
      openFirewall = true;
      # Only use userspace networking if LXC mode is enabled
      interfaceName = mkIf cfg.userspace-networking "userspace-networking";
      authKeyFile = config.sops.secrets.tailscale_auth_key.path;
      useRoutingFeatures = "client";
      permitCertUid = "caddy";
      extraUpFlags = [
        "--force-reauth"
        "--reset"
        "--ssh"
        "--accept-routes"
        "--accept-dns=true"
        # "--netfilter-mode=off"
        # "--advertise-routes=192.168.86.0/24"
      ] ++ (optionals (cfg.tags != []) [
        "--advertise-tags=${concatStringsSep "," cfg.tags}"
      ]) ++ cfg.extraFlags;
    };
    
    networking.nameservers = [ "100.100.100.100" "1.1.1.1" ];
    networking.search = [ "skink-galaxy.ts.net" ];
    
    # LXC-specific fixes
    networking.firewall.checkReversePath = mkIf cfg.lxc "loose";
    
    # Fix for local network routing conflict with Tailscale in LXC
    systemd.services.fix-tailscale-local-routing = mkIf cfg.lxc {
      description = "Remove local subnet route from Tailscale routing table";
      after = [ "tailscaled.service" ];
      wants = [ "tailscaled.service" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        RestartSec = "10s";
      };

      script = ''
        # Wait for Tailscale interface to be up
        echo "Waiting for Tailscale interface..."
        for i in {1..60}; do
          if ${pkgs.iproute2}/bin/ip link show tailscale0 > /dev/null 2>&1; then
            echo "Tailscale interface is up"
            break
          fi
          sleep 1
        done

        # Give Tailscale extra time to fully initialize and set up all routes
        echo "Waiting for Tailscale to finish route setup..."
        sleep 10

        # Continuously monitor and remove the problematic route
        while true; do
          if ${pkgs.iproute2}/bin/ip route show table 52 | ${pkgs.gnugrep}/bin/grep -q "192.168.86.0/24 dev tailscale0"; then
            ${pkgs.iproute2}/bin/ip route del 192.168.86.0/24 dev tailscale0 table 52 2>/dev/null && \
              echo "$(date): Removed 192.168.86.0/24 route from Tailscale routing table"
          fi
          sleep 5
        done
      '';
    };
  };
}