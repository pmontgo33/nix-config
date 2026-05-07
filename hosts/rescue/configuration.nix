{ config, lib, pkgs, ... }:
{
  networking.hostName = "rescue";

  users.users.nixos = {
    hashedPassword = lib.mkForce "$6$fu.ra7ConU15mC8P$kMM7PcKtpo3ruRpqncC47lbRKYK3/f2z4shsK8pewbxohu6OjpxdJP/NYLLvEg4NjN29BSt3zPq6UwSxK1Zn90";
    initialHashedPassword = lib.mkForce null;
    openssh.authorizedKeys.keys = [];
  };

  services.getty.autologinUser = lib.mkForce null;
  security.sudo.wheelNeedsPassword = lib.mkForce true;

  boot.kernelParams = [ "copytoram" ];

  environment.etc."sops/age/keys.txt" = {
    source = "/etc/sops/age/keys.txt";
    mode = "0400";
  };

  extra-services.tailscale = {
    enable = true;
    ephemeral = true;
  };
  extra-services.auto-upgrade.enable = false;

  environment.systemPackages = with pkgs; [
    opencode
    # disk / filesystem tools
    btrfs-progs
    gptfdisk
    parted
    smartmontools
    testdisk
    ddrescue
    # system tools
    htop
    lsof
    cowsay
  ];

  system.stateVersion = "25.11";
}
