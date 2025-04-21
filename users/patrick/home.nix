{ config, inputs, outputs, pkgs, ... }:

{
  home.username = "patrick";
  home.homeDirectory = "/home/patrick";

  home.packages = with pkgs; [
    kdePackages.kate
    wget
    git
    vim
    just
    cowsay
  ];



  programs.git = {
    enable = true;
    userName = "Monty";
    userEmail = "21371673+pmontgo33@users.noreply.github.com";
  };

  programs.firefox = {
    enable = true;
    profiles = {
      default = {
        id = 0;
        name = "default";
        isDefault = true;
        settings = {
          # "browser.startup.homepage" = "https://duckduckgo.com";
          "browser.search.defaultenginename" = "DuckDuckGo";
          "browser.search.order.1" = "DuckDuckGo";

          # "widget.use-xdg-desktop-portal.file-picker" = 1;
        };
        search = {
          force = true;
          default = "DuckDuckGo";
          order = [ "DuckDuckGo" "Google" ];
        };

        #TODO Add extensions: Need bitwarden and ublock-origin
      };
    };
  };



  # This value determines the home Manager release that your
  # configuration is compatible with. This helps avoid breakage
  # when a new home Manager release introduces backwards
  # incompatible changes.
  #
  # You can update home Manager without changing this value. See
  # the home Manager release notes for a list of state version
  # changes in each release.
  home.stateVersion = "24.11";

  # Let home Manager install and manage itself.
  programs.home-manager.enable = true;
}
