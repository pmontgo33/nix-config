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
      "x-systemd.requires=network-online.target"
      "noauto"
      "x-systemd.idle-timeout=600"
    ];
  };

  # Mount nextcloud NFS Share in home directory
  fileSystems."/home/patrick/mnt/nextcloud" = {
    device = "truenas:/mnt/HDD-Mirror-01/drive/patrick/files";
    fsType = "nfs";
    options = [
      "x-systemd.automount"
      "x-systemd.requires=network-online.target"
      "noauto"
      "x-systemd.idle-timeout=600"
    ];
  };

  # optional, but ensures rpc-statsd is running for on demand mounting
  boot.supportedFilesystems = [ "nfs" ];
  # Ensure network-online.target is enabled
  systemd.targets.network-online.enable = true;
}
