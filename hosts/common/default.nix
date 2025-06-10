# Common configuration for all hosts

{ lib, pkgs, inputs, outputs, ... }: {

  imports =
    [
      ./extra-services
      ../../secrets
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

  # Automatic Garbage Collection
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 7d";
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
