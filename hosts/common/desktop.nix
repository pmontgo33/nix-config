{ lib, pkgs, inputs, outputs, ... }: {


  environment.systemPackages = with pkgs; [
    libreoffice-qt
    hunspell
    hunspellDicts.en_US
  ];

  #Install flatpak
  services.flatpak.enable = true;

  # Install firefox.
  programs.firefox = {
    enable = true;
    policies = {
      DisableTelemetry = true;
      DisableFirefoxStudies = true;
      DisablePocket = true;
      SearchBar = "unified";
    };

  };

}
