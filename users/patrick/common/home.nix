{ config, inputs, outputs, pkgs, ... }:

{

  imports = [ ];

  home.username = "patrick";
  home.homeDirectory = "/home/patrick";

  home.packages = with pkgs; [
    wget
    git
    vim
    just
    #cowsay
  ];


  programs.git = {
    enable = true;
    settings = {
      user = {
        name = "Monty";
        email = "21371673+pmontgo33@users.noreply.github.com";
      };
    };
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
          "browser.search.defaultenginename" = "ddg";
          "browser.search.order.1" = "ddg";

          # "widget.use-xdg-desktop-portal.file-picker" = 1;
        };
        search = {
          force = true;
          default = "ddg";
          order = [ "ddg" "google" ];
        };

        #TODO Add extensions: Need bitwarden and ublock-origin
      };
    };
  };

  programs.ssh = {
    enable = true;
    enableDefaultConfig = false;
  };
  # programs.ssh.extraConfig = builtins.readFile ./ssh.conf;



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
