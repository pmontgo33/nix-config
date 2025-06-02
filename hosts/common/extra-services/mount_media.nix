{ lib, config, pkgs, inputs, outputs, ... }:

with lib; let
  cfg = config.extra-services.mount_media;
in {
  options.extra-services.mount_media.enable = mkEnableOption "mount the media share from TrueNAS";

  config = mkIf cfg.enable {

    fileSystems."/mnt/media" = {
      device = "truenas:/mnt/HDD-Mirror-01/media";
      fsType = "nfs";
    };
    # optional, but ensures rpc-statsd is running for on demand mounting
    #boot.supportedFilesystems = [ "nfs" ];

  };
}
