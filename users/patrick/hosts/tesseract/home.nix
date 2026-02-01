{ lib, config, pkgs, inputs, outputs, ... }:

let
  pkgs-unstable = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
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
    simplex-chat-desktop
    # inputs.nixpkgs-unstable.legacyPackages.${pkgs.system}.signal-desktop
    #cowsay
    obsidian
    # inputs.nixpkgs-unstable.legacyPackages.${pkgs.system}.notesnook ##installed via flatpak to get latest version
    # nextcloud-client
    nixos-generators
    pkgs-unstable.code-cursor
    pkgs-unstable.claude-code
    pkgs-unstable.nodejs_22 #required for claude-code
    pkgs-unstable.opencode

    # 3D printing
    (pkgs.callPackage ../../../../packages/elegoo-slicer.nix {})
  ];

  services.flatpak.enable = true;

  services.flatpak.packages = [
    "com.notesnook.Notesnook"
  ];

  services.flatpak.update.auto = {
    enable = true;
    onCalendar = "daily"; # Options: daily, weekly, monthly, or a systemd timer format like "12:00"
  };

  # Plasma 6 configuration
  programs.plasma = {
    enable = true;

    workspace = {
      theme = "breeze-dark";
    };

    panels = [
      {
        location = "bottom";
        widgets = [
          "org.kde.plasma.kickoff"
          {
            name = "org.kde.plasma.icontasks";
            config = {
              General = {
                launchers = [
                  "applications:org.kde.dolphin.desktop"
                  "applications:org.kde.konsole.desktop"
                  "applications:firefox.desktop"
                  "applications:codium.desktop"
                  "applications:com.notesnook.Notesnook.desktop"
                ];
              };
            };
          }
          "org.kde.plasma.marginsseparator"
          "org.kde.plasma.systemtray"
          "org.kde.plasma.digitalclock"
        ];
      }
    ];

    # Enable numlock on startup via config file
    configFile = {
      kcminputrc = {
        Keyboard = {
          NumLock = 0; # 0 = turn on, 1 = turn off, 2 = leave unchanged
        };
      };
      ksmserverrc = {
        General = {
          loginMode = "restorePreviousLogout"; # Restore previous session on login
        };
      };
    };
  };

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
