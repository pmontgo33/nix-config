{ config, pkgs, inputs, ... }: {

  imports = [ ];

  users.users.patrick = {
    isNormalUser = true;
    description = "Patrick";
    extraGroups = [ "networkmanager" "wheel" ];
    shell = pkgs.fish;

  };

  security.sudo.extraRules = [
    { users = [ "patrick" ];
      commands = [ { command = "ALL" ; options= [ "NOPASSWD" ]; } ];
    }
  ];

  programs.fish.enable = true;

  programs.tmux = {
    enable = true;
    extraConfig = builtins.readFile ./.dotfiles/tmux.conf;
  };

}
