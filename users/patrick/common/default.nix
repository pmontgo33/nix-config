{ config, pkgs, inputs, ... }: {

  imports = [ ../../../hosts/common/extra-services ];

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

}
