{ config, modulesPath, pkgs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  sops.secrets."pihole-api-password" = {
    mode = "0400";
    owner = "root";
    group = "root";
  };

  sops.templates."pihole-api-env" = {
    content = ''
      FTLCONF_webserver_api_password=${config.sops.placeholder."pihole-api-password"}
    '';
    mode = "0400";
    owner = "root";
    group = "root";
    restartUnits = [ "pihole-ftl.service" ];
  };

  networking.hostName = "pihole-native-test";

  services.pihole-native = {
    enable = true;
    interface = "eth0";
    # Disposable smoke-test upstreams only. Final Pi-hole targets use OPNsense :53
    # after AdGuard is retired and Unbound is moved off the mDNS port.
    upstreams = [
      "1.1.1.1"
      "9.9.9.9"
    ];
    webListenAddress = "127.0.0.1";
    apiPasswordEnvironmentFile = config.sops.templates."pihole-api-env".path;
    webPort = 8080;
    openFirewallDNS = true;
  };

  services.pihole-native.lists = [
    {
      url = "file://${pkgs.stevenblack-blocklist}/hosts";
      description = "Native NixOS Pi-hole smoke-test adlist";
      groups = [ "Default" ];
    }
  ];

  services.openssh.enable = true;

  system.stateVersion = "26.05";
}
