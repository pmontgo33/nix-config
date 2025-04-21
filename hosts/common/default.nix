# Common configuration for all hosts

{ lib, pkgs, inputs, outputs, ... }: {

  environment.systemPackages = with pkgs; [
    wget
    git
    vim
  ];


  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;


  # Enable the Flakes feature and the accompanying new nix command-line tool
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

}
