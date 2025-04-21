{ config, pkgs, inputs, ... }: {

  users.users.patrick = {
    isNormalUser = true;
    description = "Patrick";
    extraGroups = [ "networkmanager" "wheel" ];
    shell = pkgs.fish;

    packages = with pkgs; [
      #cowsay
    ];
  };

  security.sudo.extraRules = [
    { users = [ "patrick" ];
      commands = [ { command = "ALL" ; options= [ "NOPASSWD" ]; } ];
    }
  ];

  programs.fish.enable = true;

}
