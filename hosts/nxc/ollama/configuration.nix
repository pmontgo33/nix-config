{ pkgs, config, modulesPath, inputs, outputs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  environment.systemPackages = with pkgs; [
    vulkan-tools
  ];

  services.openssh.enable = true;

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;

  services.ollama = {
    enable = true;
    package = inputs.nixpkgs-unstable.legacyPackages.${pkgs.stdenv.hostPlatform.system}.ollama-vulkan;
    host = "0.0.0.0";
    openFirewall = true;
  };

  hardware.intelGpu = {
    enable = true;
    users = [ "ollama" ];
    videoAccess = true;
    computeRuntime = true;
  };

  # Ollama's Vulkan backend remains host-specific; the shared module owns the
  # common Intel graphics and render/video access layer.
  hardware.graphics.extraPackages = with pkgs; [
    mesa.drivers
    vulkan-loader
    # intel-level-zero-gpu
  ];

  environment.sessionVariables = {
    VK_ICD_FILENAMES = "${pkgs.mesa.drivers}/share/vulkan/icd.d/intel_icd.x86_64.json";
  };

  systemd.services.ollama.environment = {
    VK_ICD_FILENAMES = "${pkgs.mesa.drivers}/share/vulkan/icd.d/intel_icd.x86_64.json";
    OLLAMA_NEW_ENGINE = "1";
  };

  users.users.ollama = {
    isNormalUser = true;
    group = "ollama";
  };
  users.groups.ollama = {};

  # Open WebUI (native)
  services.open-webui = {
    enable = true;
    port = 3000;
    host = "0.0.0.0";
    environment = {
      OLLAMA_BASE_URL = "http://127.0.0.1:11434";
    };
  };

  networking.firewall.allowedTCPPorts = [ 11434 3000 ];

  system.stateVersion = "25.11";
}
