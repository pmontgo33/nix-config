{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.mount_media;
in {
  options.extra-services.mount_media = {
    enable = mkEnableOption "mount the media share from TrueNAS";

    readOnly = mkOption {
      type = types.bool;
      default = false;
      description = "Mount the NFS share as read-only";
    };
  };

  config = mkIf cfg.enable {

    fileSystems."/mnt/media" = {
      device = "192.168.86.99:/mnt/HDD-Mirror-01/media";
      fsType = "nfs";
      options = [ "x-systemd.automount" "x-systemd.after=network-online.target" ] ++ (if cfg.readOnly then [ "ro" ] else []);
    };
    # optional, but ensures rpc-statsd is running for on demand mounting
    boot.supportedFilesystems = [ "nfs" ];

  };
}
