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

}
