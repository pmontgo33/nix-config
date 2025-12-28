# Edit this configuration file to define what should be installed on
# your system. Help is available in the configuration.nix(5) man page, on
# https://search.nixos.org/options and in the NixOS manual (`nixos-help`).

{ config, lib, pkgs, ... }:

{
  imports =
    [ # Include the results of the hardware scan.
      ./hardware-configuration.nix
    ];
  
  environment.systemPackages = with pkgs; [
    inetutils
    mtr
    sysstat
  ];

  networking.hostName = "bifrost";

  users.users.patrick = {
    isNormalUser = true;
    home = "/home/patrick";
    description = "Parick";
    extraGroups = [ "wheel" "networkmanager" ];
  };

  extra-services.tailscale = {
    enable = true;
    extraFlags = ["--relay-server-port=40000"];
    # userspace-networking = true;
  };

  extra-services.host-checkin = {
    enable = true;
    isCentralHost = true;
    checkInInterval = "hourly";
  };

  services.openssh = {
    enable = true;
   #  permitRootLogin = "yes";
  };
  
  sops.secrets.cloudflare-api-token = {
    owner = "caddy";
    mode = "0400";
  };

  extra-services.caddy-proxy = {
    enable = true;
    cloudflareTokenFile = config.sops.secrets.cloudflare-api-token.path;
    
    services = {
      
      "drive.montycasa.com" = {
        protocol = "http"; 
        upstream = "http://nextcloud:80"; 
      };

      "photos.montycasa.com" = {
        protocol = "http"; 
        upstream = "http://immich:2283"; 
      };

      "audiobooks.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://audiobookshelf:13378"; 
      };

      "keep.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://karakeep:3000"; 
      };

      "mealie.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://mealie:9000"; 
      };

      "auth.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://pocket-id:1411"; 
      };
      "auth2.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://pocketid:1411"; 
      };

      "office.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://onlyoffice:80"; 
      };
      "theoffice.montycasa.com" = {
        protocol = "http";
        upstream = "http://nextcloud:80";
      };

      "endurain.montycasa.com" = { 
        protocol = "http";
        upstream = "http://endurain:8080"; 
      };

      "fit.montycasa.com" = { 
        protocol = "http";
        upstream = "http://endurain:8080"; 
      };

      "jellyfin.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://jellyfin:8096"; 
      };

      "watchit.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://jellyfin:8096"; 
      };

      "notify.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://nix-fury:8080"; 
      };

      "mollysocket.montycasa.com" = { 
        protocol = "http"; 
        upstream = "http://nix-fury:8020"; 
      };

      "ln.montybitcoin.com" = {
        protocol = "http";
        upstream = "http://bitcoin:8080";
      };
    };

    layer4SniServices = {
      # SimpleX relay server - TCP proxy via SNI
      "smp28.montycasa.com" = {
        protocol = "tcp";
        upstream = "nix-fury:5223";
      };

    #   "git.montycasa.net" = {
    #     protocol = "tcp";
    #     upstream = "192.168.86.120:22";
    #   };
    };
  };

  networking.useDHCP = false;
  networking.interfaces.eth0.useDHCP = true;
  networking.firewall.allowedUDPPorts = [ 40000 ];

  # Use the GRUB 2 boot loader.
  boot.loader.grub.enable = true;
  # boot.loader.grub.efiSupport = true;
  # boot.loader.grub.efiInstallAsRemovable = true;
  # boot.loader.efi.efiSysMountPoint = "/boot/efi";
  # Define on which hard drive you want to install Grub.
  # boot.loader.grub.device = "/dev/sda"; # or "nodev" for efi only

  # networking.hostName = "nixos"; # Define your hostname.
  # Pick only one of the below networking options.
  # networking.wireless.enable = true;  # Enables wireless support via wpa_supplicant.
  # networking.networkmanager.enable = true;  # Easiest to use and most distros use this by default.

  # Set your time zone.
  # time.timeZone = "Europe/Amsterdam";

  # Configure network proxy if necessary
  # networking.proxy.default = "http://user:password@proxy:port/";
  # networking.proxy.noProxy = "127.0.0.1,localhost,internal.domain";

  # Select internationalisation properties.
  # i18n.defaultLocale = "en_US.UTF-8";
  # console = {
  #   font = "Lat2-Terminus16";
  #   keyMap = "us";
  #   useXkbConfig = true; # use xkb.options in tty.
  # };

  # Enable the X11 windowing system.
  # services.xserver.enable = true;


  

  # Configure keymap in X11
  # services.xserver.xkb.layout = "us";
  # services.xserver.xkb.options = "eurosign:e,caps:escape";

  # Enable CUPS to print documents.
  # services.printing.enable = true;

  # Enable sound.
  # services.pulseaudio.enable = true;
  # OR
  # services.pipewire = {
  #   enable = true;
  #   pulse.enable = true;
  # };

  # Enable touchpad support (enabled default in most desktopManager).
  # services.libinput.enable = true;

  # Define a user account. Don't forget to set a password with ‘passwd’.
  # users.users.alice = {
  #   isNormalUser = true;
  #   extraGroups = [ "wheel" ]; # Enable ‘sudo’ for the user.
  #   packages = with pkgs; [
  #     tree
  #   ];
  # };

  # programs.firefox.enable = true;

  # List packages installed in system profile.
  # You can use https://search.nixos.org/ to find more packages (and options).
  # environment.systemPackages = with pkgs; [
  #   vim # Do not forget to add an editor to edit configuration.nix! The Nano editor is also installed by default.
  #   wget
  # ];

  # Some programs need SUID wrappers, can be configured further or are
  # started in user sessions.
  # programs.mtr.enable = true;
  # programs.gnupg.agent = {
  #   enable = true;
  #   enableSSHSupport = true;
  # };

  # List services that you want to enable:

  # Enable the OpenSSH daemon.
  # services.openssh.enable = true;

  # Open ports in the firewall.
  # networking.firewall.allowedTCPPorts = [ ... ];
  # networking.firewall.allowedUDPPorts = [ ... ];
  # Or disable the firewall altogether.
  # networking.firewall.enable = false;

  # Copy the NixOS configuration file and link it from the resulting system
  # (/run/current-system/configuration.nix). This is useful in case you
  # accidentally delete configuration.nix.
  # system.copySystemConfiguration = true;

  # This option defines the first version of NixOS you have installed on this particular machine,
  # and is used to maintain compatibility with application data (e.g. databases) created on older NixOS versions.
  #
  # Most users should NEVER change this value after the initial install, for any reason,
  # even if you've upgraded your system to a new NixOS release.
  #
  # This value does NOT affect the Nixpkgs version your packages and OS are pulled from,
  # so changing it will NOT upgrade your system - see https://nixos.org/manual/nixos/stable/#sec-upgrading for how
  # to actually do that.
  #
  # This value being lower than the current NixOS release does NOT mean your system is
  # out of date, out of support, or vulnerable.
  #
  # Do NOT change this value unless you have manually inspected all the changes it would make to your configuration,
  # and migrated your data accordingly.
  #
  # For more information, see `man configuration.nix` or https://nixos.org/manual/nixos/stable/options#opt-system.stateVersion .
  system.stateVersion = "25.05"; # Did you read the comment?

}

