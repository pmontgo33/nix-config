{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.mount_home_media;
in {
  options.extra-services.mount_home_media.enable = mkEnableOption "mount the home_media share from TrueNAS";

  config = mkIf cfg.enable {

    fileSystems."/mnt/home_media" = {
      device = "truenas:/mnt/HDD-Mirror-01/home_media";
      fsType = "nfs";
      options = [ "x-systemd.automount" ];
    };
    # optional, but ensures rpc-statsd is running for on demand mounting
    boot.supportedFilesystems = [ "nfs" ];

    systemd.services."remount-home_media" = {
      description = "Remount NFS share after network is up";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "/run/current-system/sw/bin/mount /mnt/home_media";
      };
      wantedBy = [ "multi-user.target" ];
    };
  };
}
