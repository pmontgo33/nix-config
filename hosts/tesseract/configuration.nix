# ThinkPad T570 Configuration
# Optimized for Intel/NVIDIA hybrid graphics, LUKS encryption, and mobile use

{ config, lib, pkgs, inputs, ... }:

{
  imports = [
    # ./hardware-configuration.nix  # Not needed - disko manages filesystems
    ./disk-config.nix
    ../../users/patrick/hosts/tesseract
    ../../users/lina/hosts/tesseract
    ../../secrets
  ];

  extra-services.desktop.enable = true;
  extra-services.tailscale.enable = true;
  # extra-services.mount_home_media.enable = true;
  extra-services.pbs-home-dirs.enable = true;
  extra-services.auto-upgrade.enable = true;

  boot = {
    loader = {
      systemd-boot.enable = true;
      efi.canTouchEfiVariables = true;
    };

    # Hybrid kernel approach: Latest with LTS fallback
    # Primary: Latest stable kernel for newest features and optimizations
    kernelPackages = pkgs.linuxPackages_latest;

    # Keep more generations in bootloader to include LTS fallback
    loader.systemd-boot.configurationLimit = 10;

    # Alternative: Uncomment to switch to LTS as primary (if latest causes issues)
    # kernelPackages = pkgs.linuxPackages_6_6;

    kernelParams = [
      "quiet"
      "splash"
      "mem_sleep_default=deep"  # Better suspend support
      "intel_pstate=active"      # Intel Pstate for better power management
      "i915.enable_fbc=1"        # Framebuffer compression
      "i915.enable_psr=1"        # Panel self refresh
      "i915.fastboot=1"          # Faster boot times
      "pcie_aspm=force"          # Force PCIe Active State Power Management
      "resume_offset=533760"      # Hybernate swap file offset
    ];

    initrd.availableKernelModules = [
      "xhci_pci"
      "nvme"
      "usb_storage"
      "sd_mod"
      "rtsx_pci_sdmmc"
      "iwlwifi"  # Intel WiFi driver
    ];

    # Enable Intel microcode updates and early KMS
    kernelModules = [ "kvm-intel" "iwlwifi" "iwlmvm" ];  # iwlwifi + iwlmvm for Intel WiFi
    initrd.kernelModules = [ "i915" ];  # Intel graphics early init for smoother boot

    # Hibernation configuration
    resumeDevice = "/dev/mapper/cryptroot";

    # Intel i915 graphics optimizations
    extraModprobeConfig = ''
      options i915 enable_guc=2
      options i915 enable_fbc=1
    '';
  };

  networking = {
    hostName = "tesseract";
    networkmanager = {
      enable = true;
      wifi = {
        powersave = true;
        scanRandMacAddress = true;
      };
      # Ensure WiFi is managed by NetworkManager
      unmanaged = [ ];
    };

    useDHCP = lib.mkDefault true;

    # Make sure wireless is enabled globally
    wireless.enable = lib.mkDefault false;  # Disable wpa_supplicant (conflicts with NetworkManager)

    # Firewall
    firewall = {
      enable = true;
      # Add any specific ports you need
      # allowedTCPPorts = [ ];
      # allowedUDPPorts = [ ];
    };
  };

  # Enable WiFi firmware for Intel AC 8265
  hardware.enableRedistributableFirmware = lib.mkForce true;

  # Explicitly include Intel WiFi firmware packages
  hardware.firmware = with pkgs; [
    wireless-regdb
    linux-firmware
    firmwareLinuxNonfree
  ];

  hardware = {
    cpu.intel.updateMicrocode = true;

    # Ensure WiFi firmware is available
    enableAllFirmware = lib.mkForce true;

    graphics = {
      enable = true;
      enable32Bit = true;
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
    # Best for battery life - NVIDIA only used when explicitly requested
    nvidia = {
      modesetting.enable = true;
      powerManagement.enable = true;
      powerManagement.finegrained = true;  # Enable dynamic power management
      open = false;  # Use proprietary driver (better support for older GPUs)
      nvidiaSettings = true;
      package = config.boot.kernelPackages.nvidiaPackages.stable;

      # NVIDIA Optimus PRIME - Offload Mode (Current)
      # Use 'nvidia-offload <command>' or 'nvidia-run <command>' to run apps with NVIDIA
      prime = {
        offload = {
          enable = true;
          enableOffloadCmd = true;  # Provides nvidia-offload command
        };

        # Alternative: Sync Mode (uncomment to use)
        # Both GPUs always active - worse battery, better compatibility
        # To switch: comment out 'offload' section above, uncomment below
        # sync.enable = true;

        # Intel HD Graphics 620: 00:02.0
        # NVIDIA GeForce 940MX: 02:00.0
        intelBusId = "PCI:0:2:0";
        nvidiaBusId = "PCI:2:0:0";
      };
    };
  };

  # Enable zram for better performance
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 50;
    priority = 100;  # Higher priority = used first (before disk swap)
  };

  # Disk swap configuration with lower priority (fallback)
  swapDevices = [{
    device = "/swap/swapfile";
    priority = 10;  # Lower priority = used after zram is full
  }];

  services = {
    # Enable power-profiles-daemon for GUI profile switching in KDE
    power-profiles-daemon.enable = true;

    # Periodical TRIM for SSD longevity and performance
    fstrim.enable = true;

    # Btrfs automatic scrubbing for data integrity
    btrfs.autoScrub = {
      enable = true;
      interval = "monthly";
      fileSystems = [ "/" ];
    };

    # udev rules for better power management
    # udev.extraRules = ''
    #   # Disable wake-on-LAN to save power
    #   ACTION=="add", SUBSYSTEM=="net", KERNEL=="eth*", RUN+="${pkgs.ethtool}/bin/ethtool -s $name wol d"

    #   # Auto-suspend USB devices for better battery life
    #   ACTION=="add", SUBSYSTEM=="usb", TEST=="power/control", ATTR{power/control}="auto"
    # '';

    # Thermal management
    thermald.enable = true;

    # Firmware updates
    fwupd.enable = true;

    # Auto-suspend/hibernate on lid close
    logind = {
      lidSwitch = "hibernate";  # On battery: hibernate immediately
      lidSwitchDocked = "ignore";
      lidSwitchExternalPower = "suspend-then-hibernate";  # On AC: sleep then hibernate
    };
  };

  security.tpm2 = {
    enable = true;
    pkcs11.enable = true;
    tctiEnvironment.enable = true;
  };

  # Use systemd in initrd (required for TPM2)
  boot.initrd.systemd.enable = true;

  # Systemd optimizations for faster shutdown/reboot
  systemd.extraConfig = ''
    DefaultTimeoutStopSec=10s
  '';

  # Enable systemd-oomd for better memory pressure handling
  systemd.oomd = {
    enable = true;
    enableRootSlice = true;
    enableUserSlices = true;
  };

  services.xserver = {
    enable = true;
    videoDrivers = [ "nvidia" "modesetting" ];

    # Keyboard layout
    xkb = {
      layout = "us";
      variant = "";
    };
  };

  # Display Manager (moved out of xserver in 25.05)
  services.displayManager.sddm = {
    enable = true;
    autoNumlock = true;
  };

  # Session startup commands (for Plasma/X11 sessions)
  services.xserver.displayManager.sessionCommands = ''
    ${pkgs.numlockx}/bin/numlockx on
  '';

  # Touchpad configuration (moved out of xserver in 25.05)
  services.libinput = {
    enable = true;
    touchpad = {
      naturalScrolling = true;
      tapping = true;
      disableWhileTyping = true;
      accelProfile = "adaptive";
    };
  };

  services.desktopManager.plasma6.enable = true;

  # Audio configuration
  services.pulseaudio.enable = false;  # Using PipeWire instead
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  services.printing = {
    enable = true;
    # drivers = [ pkgs.canon-cups-ufr2 ];  # Disabled - causes build issues and may not be needed
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
    # ansible-lint  # Temporarily disabled due to dependency conflict in nixpkgs 25.05
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
    nvtopPackages.nvidia  # Use nvidia variant instead of full (avoids CUDA dependencies)

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