{ config, lib, pkgs, ... }:

let
  cfg = config.hardware.intelGpu;
in
{
  options.hardware.intelGpu = {
    enable = lib.mkEnableOption "Intel GPU support for service workloads";

    users = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Users that need access to the mapped Intel render device.";
    };

    # `render` is retained as a migration escape hatch for Immich, whose
    # mutable guest database already has that group at the host GID.
    accessGroup = lib.mkOption {
      type = lib.types.enum [ "renderaccess" "render" ];
      default = "renderaccess";
      description = "Guest group used for access to the host render device.";
    };

    legacyAccessGroup = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Allow migration from a pre-existing mutable standard render group.";
    };

    renderGid = lib.mkOption {
      type = lib.types.ints.positive;
      default = 104;
      description = "Host numeric GID for /dev/dri/renderD128.";
    };

    videoAccess = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Create a group for access to /dev/dri/card* devices.";
    };

    videoGid = lib.mkOption {
      type = lib.types.ints.positive;
      default = 44;
      description = "Host numeric GID for /dev/dri/card* devices.";
    };

    computeRuntime = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Install Intel Compute Runtime for Intel compute workloads.";
    };

    videoProcessing = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Install Intel VPL GPU runtime for video-processing workloads.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.users != [ ];
        message = "hardware.intelGpu.users must not be empty when Intel GPU support is enabled.";
      }
      {
        assertion = !cfg.legacyAccessGroup || cfg.accessGroup == "render";
        message = "hardware.intelGpu.legacyAccessGroup requires accessGroup = \"render\".";
      }
      {
        assertion = !cfg.videoAccess || cfg.accessGroup != "videoaccess";
        message = "hardware.intelGpu.accessGroup cannot be videoaccess when videoAccess is enabled.";
      }
      {
        assertion = cfg.accessGroup == "render" || lib.attrByPath [ "render" "gid" ] null config.users.groups != cfg.renderGid;
        message = "hardware.intelGpu.renderGid conflicts with the declarative standard render group; use accessGroup = \"render\" only for an explicit legacy migration.";
      }
      {
        assertion = !cfg.videoAccess || lib.attrByPath [ "video" "gid" ] null config.users.groups != cfg.videoGid;
        message = "hardware.intelGpu.videoGid conflicts with the declarative standard video group.";
      }
      {
        assertion = !cfg.videoAccess || cfg.renderGid != cfg.videoGid;
        message = "hardware.intelGpu.renderGid and videoGid must differ when videoAccess is enabled.";
      }
    ];

    hardware.graphics = {
      enable = true;
      extraPackages = with pkgs; [
        intel-media-driver
        intel-vaapi-driver
        libva-vdpau-driver
        libvdpau-va-gl
      ]
      ++ lib.optional cfg.computeRuntime intel-compute-runtime
      ++ lib.optional cfg.videoProcessing vpl-gpu-rt;
    };

    users.groups = {
      ${cfg.accessGroup} = {
        gid = if cfg.legacyAccessGroup then lib.mkForce cfg.renderGid else cfg.renderGid;
        members = cfg.users;
      };
    } // lib.optionalAttrs cfg.videoAccess {
      videoaccess = {
        gid = cfg.videoGid;
        members = cfg.users;
      };
    };
  };
}
