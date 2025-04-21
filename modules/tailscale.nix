/*
For installation on LXC, add these lines to /etc/pve/lxc/ID.conf:
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file

For LXC, run:
tailscale up --ssh --accept-dns=false
For other, run:
tailscale up --ssh

*/

{
  imports = [
    ../modules/secrets.nix
  ];

  services.tailscale = {
    enable = true;
#    interfaceName = "userspace-networking";
    openFirewall = true;
    # authKeyFile = "/etc/nixos/.tailscale-auth-key"; # Place authkey file at root directory of configuration.nix
    authKeyFile = "/run/secrets/tailscale-auth-key";
    extraUpFlags = [
      "--force-reauth"
      "--reset"
      "--ssh"
#      "--accept-dns=false"
    ];
  };

#  networking.nameservers = [ "100.100.100.100"];
  networking.nameservers = [ "100.100.100.100" "1.1.1.1" "1.0.0.1" ];
  networking.search = [ "skink-galaxy.ts.net" ];
}
