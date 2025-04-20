{ config, pkgs, inputs, ... }: {

    users.users.patrick = {
    isNormalUser = true;
    description = "Patrick";
    extraGroups = [ "networkmanager" "wheel" ];
    packages = with pkgs; [

#       cowsay
    ];
#     packages = [inputs.home-manager.packages.${pkgs.system}.default];
#     home-manager.users.patrick = import ../../home/patrick/home.nix; #######THIS LINE IS CREATING AN ERROR
  };
        #packages = [inputs.home-manager.packages.${pkgs.system}.default];
#     home-manager.users.m3tam3re =
#         import ../../../home/m3tam3re/${config.networking.hostName}.nix;


    security.sudo.extraRules = [
    { users = [ "patrick" ];
      commands = [
        { command = "ALL" ;
	  options= [ "NOPASSWD" ]; # "SETENV" # Adding the following could be a good idea
	}
      ];
    }
  ];

}
