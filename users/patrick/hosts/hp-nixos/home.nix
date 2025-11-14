{ lib, config, pkgs, inputs, outputs, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.system;
    config.allowUnfree = true;
  };
in
{
  imports = [ ../../common/home.nix ];

  # Enable automatic updates in KDE Discover
  xdg.configFile."discoverrc".text = ''
    [Global]
    UseUnattendedUpdates=true
  '';

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
    pkgs-unstable.code-cursor
    pkgs-unstable.claude-code
    pkgs-unstable.nodejs_22 #required for claude-code
  ];

  programs.vscode = {
    enable = true;
    package = pkgs-unstable.vscodium;
    profiles.default.extensions = with pkgs.vscode-extensions; [
      jnoortheen.nix-ide
      redhat.ansible
      redhat.vscode-yaml # dependency for redhat.ansible
      ms-python.python # dependency for redhat.ansible
      samuelcolvin.jinjahtml
      # pkgs-unstable.vscode-extensions.anthropic.claude-code
    ];
  };
}
