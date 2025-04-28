{ config, pkgs, inputs, outputs, ... }:
# let
#   home-manager = builtins.fetchTarball "https://github.com/nix-community/home-manager/archive/release-24.05.tar.gz";
# in
{
  imports = [
    (import "home-manager/nixos")
  ];

  users.users.patrick = {
    isNormalUser = true;
    extraGroups = [ "wheel" ]; # Enable ‘sudo’ for the user.
    openssh.authorizedKeys.keys = [
      "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCrywx2ymEVjJR99l31CoyiyoqWQfOib8V3n3j3ZWp42aNRi0HgsoPvBqtZMmM33HLkPmylkiyGP4wggfhdQV9kQIacaKvPja5V8uFxhVXOmR2wIxh9OmegD1z5tsLi+bxbANdpZGPrxUp9fq4KIvLRlM7ckrhuGHE/rgkDs2qCVsXzIG9uBourUblRCUEigkxtj6wQqKo4XhvEERq0aRwI0RttVkEWYfGw99PLuUahfzlbBvuOuh0EwsY7e2qIXkjCNoMbgGHV6hqe0XEwEud/GlFehBl3bh9D2k6UnmR21nINtrc/2mh54pNYyFl80HdmU0/IBPdhUxLmB9XBSVrb work-hp-zbook"
    ];
  };
	
  security.sudo.extraRules = [
    { users = [ "patrick" ];
      commands = [
        { command = "ALL" ;
	  options= [ "NOPASSWD" ]; # "SETENV" # Adding the following could be a good idea
	}
      ];
    }
  ];

  ### OPENSSH ###
  services.openssh.enable = true;
#  services.openssh.settings.AllowUsers = ["patrick"];
#  services.openssh.settings.PermitRootLogin = "yes";
	
  services.openssh.hostKeys = [ #THIS IS NOT GENERATING A NEW SSH KEY
    {
      path = "/home/patrick/.ssh/id_ed25519";
      type = "ed25519";
    }
  ];

  home-manager.users.patrick = {
    /* The home.stateVersion option does not have a default and must be set */
    home.stateVersion = "18.09";
    
      programs.git = {
        enable = true;
        userName  = "Monty";
        userEmail = "21371673+pmontgo33@users.noreply.github.com";
        extraConfig = {
          receive.denyCurrentBranch = "updateInstead"; 
        };
      };
  };
}
