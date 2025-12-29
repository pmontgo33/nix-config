# Common configuration for all hosts

{ lib, pkgs, inputs, outputs, ... }: {

  imports = [
      ../../modules
      ../../secrets
  ];

  extra-services.auto-upgrade.enable = true;

  environment.systemPackages = with pkgs; [
    wget
    git
    vim
    just
    fail2ban
  ];

  # Set Timezone
  time.timeZone = "America/New_York";

  # Set the host platform (replaces deprecated 'system' parameter)
  nixpkgs.hostPlatform = "x86_64-linux";

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
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMa5HxhMxXea3SH+hxZbr0XAxenGnl42GgQTzdXNbQSW Pixel 9"
      ];
    shell = pkgs.fish;
  };

  programs.fish.enable = true;

  system.activationScripts.copyJustfile = ''
    cp -f ${./.dotfiles/justfile} /root/justfile
    chmod 644 /root/justfile
  '';

  # Automatic Garbage Collection
  programs.nh = {
    enable = true;
    clean = {
      enable = true;
      dates = "weekly";
      extraArgs = "--keep-since 14d --keep 5";
    };
    flake = "github:pmontgo33/nix-config";
  };

}
