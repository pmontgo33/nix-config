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
    options = [ "x-systemd.automount" ];
  };

  systemd.services."remount-patrick-media" = {
    description = "Remount NFS share after network is up";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "/run/current-system/sw/bin/mount /home/patrick/mnt/media";
    };
    wantedBy = [ "multi-user.target" ];
  };

  # Mount nextcloud NFS Share in home directory
  fileSystems."/home/patrick/mnt/nextcloud" = {
    device = "truenas:/mnt/HDD-Mirror-01/drive/patrick/files";
    fsType = "nfs";
    options = [ "x-systemd.automount" ];
  };
  systemd.services."remount-patrick-nextcloud" = {
    description = "Remount NFS share after network is up";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "/run/current-system/sw/bin/mount /home/patrick/mnt/nextcloud";
    };
    wantedBy = [ "multi-user.target" ];
  };

  # optional, but ensures rpc-statsd is running for on demand mounting
  boot.supportedFilesystems = [ "nfs" ];
  # Ensure network-online.target is enabled
  systemd.targets.network-online.enable = true;
}
