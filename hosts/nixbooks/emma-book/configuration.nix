{ config, lib, pkgs, ... }:

{

  imports = [ 
      ./hardware-configuration.nix
  ];

  networking.hostName = "emma-book"; # Define your hostname.

  # Filesystem configuration
  fileSystems."/home" = {
    device = "/dev/disk/by-uuid/0f2ce08a-5508-4532-b82e-c9007e22776d";
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
  users.users.emma = {
    isNormalUser = true;
    description = "Emma";
    extraGroups = [ "networkmanager" "wheel" "storage" ];
    packages = with pkgs; [
    #  thunderbird
      simplex-chat-desktop
      ponysay
    ];
  };

  environment.systemPackages = with pkgs; [
    
  ];

  system.stateVersion = "25.05";

}
