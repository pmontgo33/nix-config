# ThinkPad T570 Configuration
# Optimized for Intel/NVIDIA hybrid graphics, LUKS encryption, and mobile use

{ config, pkgs, inputs, ... }:

{
  imports = [
    ./hardware-configuration.nix
    ./disk-config.nix
    ../../users/patrick/hosts/t570
    ../../users/lina/hosts/t570
    ../../secrets
  ];

  extra-services.desktop.enable = true;
  extra-services.tailscale.enable = true;
  extra-services.mount_home_media.enable = true;
  extra-services.pbs-home-dirs.enable = true;
  extra-services.auto-upgrade.enable = true;

  boot = {
    loader = {
      systemd-boot.enable = true;
      efi.canTouchEfiVariables = true;
    };

    kernelPackages = pkgs.linuxPackages_latest;

    kernelParams = [
      "quiet"
      "splash"
      "mem_sleep_default=deep"  # Better suspend support
    ];

    initrd.availableKernelModules = [
      "xhci_pci"
      "nvme"
      "usb_storage"
      "sd_mod"
      "rtsx_pci_sdmmc"
    ];

    # Enable Intel microcode updates
    kernelModules = [ "kvm-intel" ];
  };

  networking = {
    hostName = "tesseract";
    networkmanager.enable = true;

    # Firewall
    firewall = {
      enable = true;
      # Add any specific ports you need
      # allowedTCPPorts = [ ];
      # allowedUDPPorts = [ ];
    };
  };

  hardware = {
    cpu.intel.updateMicrocode = true;

    opengl = {
      enable = true;
      driSupport = true;
      driSupport32Bit = true;
      extraPackages = with pkgs; [
        intel-media-driver  # LIBVA_DRIVER_NAME=iHD
        vaapiIntel          # LIBVA_DRIVER_NAME=i965 (older but sometimes better)
        vaapiVdpau
        libvdpau-va-gl
      ];
    };

    bluetooth = {
      enable = true;
      powerOnBoot = true;
      settings = {
        General = {
          Enable = "Source,Sink,Media,Socket";
        };
      };
    };

    trackpoint = {
      enable = true;
      emulateWheel = true;
      sensitivity = 220;
      speed = 97;
    };

    # NVIDIA Configuration - Prime Offload (On-Demand)
    nvidia = {
      modesetting.enable = true;
      powerManagement.enable = true;
      powerManagement.finegrained = true;  # Enable dynamic power management
      open = false;  # Use proprietary driver (better support for older GPUs)
      nvidiaSettings = true;
      package = config.boot.kernelPackages.nvidiaPackages.stable;

      # NVIDIA Optimus PRIME
      prime = {
        offload = {
          enable = true;
          enableOffloadCmd = true;  # Provides nvidia-offload command
        };

        # Get these with: lspci | grep VGA
        # Update after first boot if needed
        intelBusId = "PCI:0:2:0";
        nvidiaBusId = "PCI:1:0:0";
      };
    };
  };

  services = {
    # TLP for better battery life
    tlp = {
      enable = true;
      settings = {
        # CPU scaling
        CPU_SCALING_GOVERNOR_ON_AC = "performance";
        CPU_SCALING_GOVERNOR_ON_BAT = "powersave";

        CPU_ENERGY_PERF_POLICY_ON_AC = "performance";
        CPU_ENERGY_PERF_POLICY_ON_BAT = "power";

        CPU_MIN_PERF_ON_AC = 0;
        CPU_MAX_PERF_ON_AC = 100;
        CPU_MIN_PERF_ON_BAT = 0;
        CPU_MAX_PERF_ON_BAT = 50;

        # Battery care (extends battery life)
        START_CHARGE_THRESH_BAT0 = 40;
        STOP_CHARGE_THRESH_BAT0 = 80;

        START_CHARGE_THRESH_BAT1 = 40;
        STOP_CHARGE_THRESH_BAT1 = 80;

        # Disable turbo on battery
        CPU_BOOST_ON_AC = 1;
        CPU_BOOST_ON_BAT = 0;

        # Runtime power management
        RUNTIME_PM_ON_AC = "auto";
        RUNTIME_PM_ON_BAT = "auto";

        # USB autosuspend
        USB_AUTOSUSPEND = 1;

        # SATA link power management
        SATA_LINKPWR_ON_AC = "max_performance";
        SATA_LINKPWR_ON_BAT = "min_power";
      };
    };

    # Thermal management
    thermald.enable = true;

    # Firmware updates
    fwupd.enable = true;

    # Auto-suspend on lid close
    logind = {
      lidSwitch = "suspend";
      lidSwitchDocked = "ignore";
      lidSwitchExternalPower = "suspend";
    };
  };

  security.tpm2 = {
    enable = true;
    pkcs11.enable = true;
    tctiEnvironment.enable = true;
  };

  # Use systemd in initrd (required for TPM2)
  boot.initrd.systemd.enable = true;

  services.xserver = {
    enable = true;
    videoDrivers = [ "nvidia" "intel" ];

    # KDE Plasma 6
    displayManager = {
      sddm = {
        enable = true;
        autoNumlock = true;
      };
      sessionCommands = ''
        ${pkgs.numlockx}/bin/numlockx on
      '';
    };

    # Keyboard layout
    xkb = {
      layout = "us";
      variant = "";
    };

    # Touchpad
    libinput = {
      enable = true;
      touchpad = {
        naturalScrolling = true;
        tapping = true;
        disableWhileTyping = true;
        accelProfile = "adaptive";
      };
    };
  };

  services.desktopManager.plasma6.enable = true;

  sound.enable = true;
  hardware.pulseaudio.enable = false;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  services.printing = {
    enable = true;
    drivers = [ pkgs.canon-cups-ufr2 ];
    browsing = true;
  };

  services.avahi = {
    enable = true;
    nssmdns4 = true;
    publish = {
      enable = true;
      workstation = true;
      addresses = true;
    };
  };

  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "prohibit-password";
      PasswordAuthentication = false;
    };
  };

  services.fprintd = {
    enable = true;
    # T570 uses Validity fingerprint sensor
  };

  environment.systemPackages = with pkgs; [
    # From hp-nixos
    ansible
    ansible-lint
    terraform
    sshpass
    vlc
    jq
    numlockx

    # ThinkPad specific tools
    lm_sensors
    powertop
    acpi
    tpm2-tools

    # NVIDIA tools
    nvtopPackages.full

    # Hibernation tools
    pmutils

    # Intel GPU tools
    intel-gpu-tools
    libva-utils
  ];

  environment.variables = {
    # Use Intel GPU by default
    # For NVIDIA, use: nvidia-offload <command>
    LIBVA_DRIVER_NAME = "iHD";  # or "i965" if iHD doesn't work well
  };

  environment.shellAliases = {
    # Run applications with NVIDIA GPU
    nvidia-run = "nvidia-offload";
  };

  time.timeZone = "America/New_York";

  i18n = {
    defaultLocale = "en_US.UTF-8";
    extraLocaleSettings = {
      LC_ADDRESS = "en_US.UTF-8";
      LC_IDENTIFICATION = "en_US.UTF-8";
      LC_MEASUREMENT = "en_US.UTF-8";
      LC_MONETARY = "en_US.UTF-8";
      LC_NAME = "en_US.UTF-8";
      LC_NUMERIC = "en_US.UTF-8";
      LC_PAPER = "en_US.UTF-8";
      LC_TELEPHONE = "en_US.UTF-8";
      LC_TIME = "en_US.UTF-8";
    };
  };

  system.stateVersion = "25.05";
}