{ modulesPath, pkgs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

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
    webPort = 8080;
    openFirewallDNS = true;
  };

  services.pihole-ftl.lists = [
    {
      url = "file://${pkgs.stevenblack-blocklist}/hosts";
      description = "Native NixOS Pi-hole smoke-test adlist";
    }
  ];

  services.openssh.enable = true;

  system.stateVersion = "26.05";
}
