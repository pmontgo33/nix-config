{ config, lib, pkgs, ... }:

{

  imports = [ 
      ./hardware-configuration.nix
  ];

  networking.hostName = "ali-book"; # Define your hostname.
  
  # Filesystem configuration
  fileSystems."/home" = {
    device = "/dev/disk/by-uuid/fcf87905-cdb2-48d3-bf33-7e47d50e33f4";
    fsType = "ext4";
    options = [ 
      "defaults" 
      "user_xattr" 
      "acl" 
      "noatime"        # Reduce write operations
      "commit=60"      # Commit changes every 60 seconds
    ];
  };

  # Define a user account. Don't forget to set a password with ‘passwd’.
  users.users.aleandra = {
    isNormalUser = true;
    description = "Aleandra";
    extraGroups = [ "networkmanager" "wheel" "storage" ];
    packages = with pkgs; [
    #  thunderbird
      simplex-chat-desktop
      cowsay
    ];
  };

  environment.systemPackages = with pkgs; [
    
  ];

  system.stateVersion = "25.05";

}
