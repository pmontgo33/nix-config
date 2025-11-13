{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.desktop;
in {
  options.extra-services.desktop.enable = mkEnableOption "enable desktop config";

  config = mkIf cfg.enable {

    environment.systemPackages = with pkgs; [
      libreoffice-qt
      hunspell
      hunspellDicts.en_US
      kdePackages.kolourpaint
      kdePackages.kcalc
    ];

   services.flatpak.enable = true;

    programs.firefox = {
      enable = true;
      policies = {
        DisableTelemetry = true;
        DisableFirefoxStudies = true;
        DisablePocket = true;
        SearchBar = "unified";
      };

    };

  };
}
