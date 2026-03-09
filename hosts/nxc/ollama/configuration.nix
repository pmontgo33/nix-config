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
    host = "0.0.0.0";
    openFirewall = true;
  };

  hardware.graphics = {
    enable = true;
    extraPackages = with pkgs; [
      intel-media-driver
      intel-compute-runtime
      mesa.drivers
      vulkan-loader
      # intel-level-zero-gpu
    ];
  };

  environment.sessionVariables = {
    VK_ICD_FILENAMES = "${pkgs.mesa.drivers}/share/vulkan/icd.d/intel_icd.x86_64.json";
  };

  systemd.services.ollama.environment = {
    VK_ICD_FILENAMES = "${pkgs.mesa.drivers}/share/vulkan/icd.d/intel_icd.x86_64.json";
  };

  users.users.ollama = {
    isNormalUser = true;
    group = "ollama";
    extraGroups = [ "video" "render" ];
  };
  users.groups.ollama = {};
  users.groups.renderaccess = {
    gid = 104;
    members = [ "ollama" ];
  };

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
