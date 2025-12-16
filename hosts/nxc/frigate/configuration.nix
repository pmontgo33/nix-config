{ config, pkgs, modulesPath, inputs, outputs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  # No extra packages needed - Frigate includes go2rtc

  networking.hostName = "frigate";

  services.openssh.enable = true;

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;

  systemd.tmpfiles.rules = [
    "d /mnt/media 0755 root root -"
    "d /var/lib/frigate/cache 0755 frigate frigate -"
    "d /var/lib/frigate/model_cache 0755 frigate frigate -"
    "d /media 0755 root root -"
    "L+ /media/frigate - - - - /var/lib/frigate"
  ];

  extra-services.mount_media.enable = true;

  # Configure SOPS secrets for Frigate
  sops.secrets.frigate-env = {};

  # Go2RTC is now managed by the Frigate container

  # Enable Podman for running Frigate OCI container
  virtualisation.podman = {
    enable = true;
    # Required for containers to access host network
    defaultNetwork.settings.dns_enabled = true;
  };

  # Frigate NVR as OCI Container
  virtualisation.oci-containers = {
    backend = "podman";
    containers.frigate = {
      image = "ghcr.io/blakeblackshear/frigate:0.16.0";

      # Use host network mode for easier access to go2rtc
      extraOptions = [
        "--network=host"
        "--shm-size=256m"
        "--device=/dev/dri/renderD128:/dev/dri/renderD128"
        "--device=/dev/dri/card0:/dev/dri/card0"
        "--group-add=104"  # renderaccess
        "--group-add=44"   # videoaccess
        "--cap-add=CAP_PERFMON"  # Performance monitoring capability
        "--tmpfs=/tmp/cache:size=1G"  # Tmpfs for cache (reduces disk wear)
      ];

      volumes = [
        "/var/lib/frigate:/var/lib/frigate"
        "/var/lib/frigate:/media/frigate"  # Backward compatibility for old database paths
        "/run/frigate-config:/config"
        "/var/lib/frigate/model_cache:/config/model_cache"
        "/etc/localtime:/etc/localtime:ro"  # Match host timezone
      ];

      environment = {
        LIBVA_DRIVER_NAME = "iHD";
        FRIGATE_MQTT_PASSWORD = "\${FRIGATE_MQTT_PASSWORD}";
        FRIGATE_CAMERA_PASSWORD = "\${FRIGATE_CAMERA_PASSWORD}";
      };

      environmentFiles = [
        config.sops.secrets.frigate-env.path
      ];
    };
  };

  # Create runtime config directory and substitute secrets
  systemd.services.podman-frigate = {
    preStart = ''
      # Create config directory
      mkdir -p /run/frigate-config

      # Load environment variables
      set -a
      source ${config.sops.secrets.frigate-env.path}
      set +a

      # Substitute environment variables in config
      ${pkgs.gnused}/bin/sed \
        -e "s/{FRIGATE_MQTT_PASSWORD}/$FRIGATE_MQTT_PASSWORD/g" \
        -e "s/{FRIGATE_CAMERA_PASSWORD}/$FRIGATE_CAMERA_PASSWORD/g" \
        ${./frigate-config.yml} > /run/frigate-config/config.yml
    '';

    # Ensure NFS mount is available before starting Frigate
    requires = [ "mnt-media.mount" ];
    after = [ "mnt-media.mount" ];
  };

  # GPU access for LXC - match host device GIDs
  users.groups.renderaccess = {
    gid = 104;
    members = [ "frigate" ];
  };
  users.groups.videoaccess = {
    gid = 44;
    members = [ "frigate" ];
  };

  # Enable hardware graphics support
  hardware.graphics = {
    enable = true;
    extraPackages = with pkgs; [
      intel-media-driver
      intel-vaapi-driver
      libva-vdpau-driver
      vpl-gpu-rt
    ];
  };

  # Bind mount NFS recordings and exports to Frigate directories
  fileSystems."/var/lib/frigate/recordings" = {
    device = "/mnt/media/frigate/recordings";
    fsType = "none";
    options = [ "bind" ];
  };

  fileSystems."/var/lib/frigate/exports" = {
    device = "/mnt/media/frigate/exports";
    fsType = "none";
    options = [ "bind" ];
  };


  # Mount tmpfs for cache (reduces SSD wear)
  fileSystems."/var/lib/frigate/cache" = {
    device = "tmpfs";
    fsType = "tmpfs";
    options = [ "size=1G" "mode=0755" ];
  };

  # Frigate container exposes port 5000 directly via --network=host

  # Open firewall ports for Frigate
  networking.firewall.allowedTCPPorts = [
    5000   # Web UI
    8554   # RTSP feeds
    8555   # WebRTC TCP
    1984   # Go2RTC API
    8971   # Additional Go2RTC port
  ];
  networking.firewall.allowedUDPPorts = [
    8555   # WebRTC UDP
  ];

  system.stateVersion = "25.05";
}
