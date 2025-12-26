{ config, pkgs, inputs, ... }: {

  imports = [ ../../common ];
  
  users.users.patrick = {

      packages = with pkgs; [
        nfs-utils
        standardnotes
        #cowsay
      ];
  };

  # Mount media NFS Share in home directory
  fileSystems."/home/patrick/mnt/media" = {
    device = "truenas:/mnt/HDD-Mirror-01/media";
    fsType = "nfs";
    options = [
      "x-systemd.automount"
      "noauto"
      "nofail"
      "_netdev"
      "x-systemd.idle-timeout=600"
      "x-systemd.device-timeout=5"
    ];
  };

  # Mount home_media NFS Share in home directory
  fileSystems."/home/patrick/mnt/home_media" = {
    device = "truenas:/mnt/HDD-Mirror-01/home_media";
    fsType = "nfs";
    options = [
      "x-systemd.automount"
      "noauto"
      "nofail"
      "_netdev"
      "x-systemd.idle-timeout=600"
      "x-systemd.device-timeout=5"
    ];
  };

  # Mount nextcloud NFS Share in home directory
  fileSystems."/home/patrick/mnt/nextcloud" = {
    device = "truenas:/mnt/HDD-Mirror-01/drive/patrick/files";
    fsType = "nfs";
    options = [
      "x-systemd.automount"
      "noauto"
      "nofail"
      "_netdev"
      "x-systemd.idle-timeout=600"
      "x-systemd.device-timeout=5"
    ];
  };

  # Create mount point directories if they don't exist
  systemd.tmpfiles.rules = [
    "d /home/patrick/mnt 0755 patrick users -"
    "d /home/patrick/mnt/media 0755 patrick users -"
    "d /home/patrick/mnt/home_media 0755 patrick users -"
    "d /home/patrick/mnt/nextcloud 0755 patrick users -"
  ];

  # optional, but ensures rpc-statsd is running for on demand mounting
  boot.supportedFilesystems = [ "nfs" ];
  # Ensure network-online.target is enabled
  systemd.targets.network-online.enable = true;
}
