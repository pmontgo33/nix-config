{ config, lib, pkgs, ... }:
{
  networking.hostName = "rescue";

  boot.kernelParams = [ "copytoram" ];

  extra-services.tailscale.enable = true;
  extra-services.auto-upgrade.enable = false;

  environment.systemPackages = with pkgs; [
    opencode
    # disk / filesystem tools
    btrfs-progs
    gptfdisk
    parted
    smartmontools
    # system tools
    htop
    lsof
  ];

  system.stateVersion = "25.11";
}
