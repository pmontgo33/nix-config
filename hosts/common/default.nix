# Common configuration for all hosts

{ lib, pkgs, inputs, outputs, ... }: {

  imports =
    [
      ./extra-services
    ];

  environment.systemPackages = with pkgs; [
    wget
    git
    vim
    just
    fail2ban
  ];

  # Set Timezone
  time.timeZone = "America/New_York";

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # Enable the Flakes feature and the accompanying new nix command-line tool
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Add public keys for management machines
  users.users.root = {
    hashedPassword = "$6$fu.ra7ConU15mC8P$kMM7PcKtpo3ruRpqncC47lbRKYK3/f2z4shsK8pewbxohu6OjpxdJP/NYLLvEg4NjN29BSt3zPq6UwSxK1Zn90";
    openssh.authorizedKeys.keys = [
        # Management public keys
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEr9aBBJ73I/tXOT00krxHglmAqZ0A8xt7Hk5s2zMwCo patrick@hp-nixos"
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE+WBmDc0ACtIS4DZl2fHyFCxxAMIa6c5PuMgvuSBD5R patrick@nix-fury"
      ];
  };

  # Automatic Garbage Collection
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 14d";
  };

  system.autoUpgrade = {
    enable = true;
    flake = inputs.self.outPath;
    flags = [
      "--update-input" "nixpkgs"
      "--update-input" "nixpkgs-unstable"
      "--update-input" "home-manager"
      "--commit-lock-file"
      "-L" # print build logs
    ];
    dates = "weekly";
    randomizedDelaySec = "45min";
    persistent = true;
    operation = "boot";
  };

}
