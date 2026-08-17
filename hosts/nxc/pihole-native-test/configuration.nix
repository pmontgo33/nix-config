{ modulesPath, pkgs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  networking.hostName = "pihole-native-test";

  services.pihole-native = {
    enable = true;
    interface = "eth0";
    upstreams = [ "192.168.86.1#5353" ];
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
