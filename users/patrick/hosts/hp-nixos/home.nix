{ lib, config, pkgs, inputs, outputs, ... }:

{
  imports = [ ../../common/home.nix ];

  home.packages = with pkgs; [
    kdePackages.kate
    google-chrome
    signal-desktop
    # inputs.nixpkgs-unstable.legacyPackages.${pkgs.system}.signal-desktop
    #cowsay
    obsidian
    # inputs.nixpkgs-unstable.legacyPackages.${pkgs.system}.notesnook ##installed via flatpak to get latest version
    # nextcloud-client
    nixos-generators
  ];

  programs.vscode = {
    enable = true;
    package = pkgs.vscodium;
    profiles.default.extensions = with pkgs.vscode-extensions; [
      jnoortheen.nix-ide
      redhat.ansible
      redhat.vscode-yaml # dependency for redhat.ansible
      ms-python.python # dependency for redhat.ansible
      samuelcolvin.jinjahtml
    ];
  };
}
