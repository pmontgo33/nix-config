{ config, pkgs, modulesPath, inputs, outputs, ... }:

{
  imports = [
    (modulesPath + "/virtualisation/proxmox-lxc.nix")
  ];

  extra-services.tailscale = {
    enable = true;
    lxc = true;
  };
  extra-services.host-checkin.enable = true;
  extra-services.mount_notes.enable = true;

  services.openssh.enable = true;

  # Packages for headless Obsidian
  environment.systemPackages = with pkgs; [
    obsidian
    openbox
    xorg.xorgserver
    x11vnc
  ];

  # Increase inotify limits for Obsidian file watching
  boot.kernel.sysctl = {
    "fs.inotify.max_user_watches" = 524288;
    "fs.inotify.max_user_instances" = 1024;
  };

  # Systemd services for headless Obsidian
  systemd.services.obsidian-xvfb = {
    description = "Xvfb virtual framebuffer for Obsidian";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.xorg.xorgserver}/bin/Xvfb :99 -screen 0 1024x768x16 -extension GLX";
      Restart = "on-failure";
      RestartSec = "10s";
    };
  };

  systemd.services.obsidian-openbox = {
    description = "Openbox window manager for Obsidian";
    wantedBy = [ "multi-user.target" ];
    after = [ "obsidian-xvfb.service" ];
    requires = [ "obsidian-xvfb.service" ];
    environment = {
      DISPLAY = ":99";
    };
    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.openbox}/bin/openbox";
      Restart = "on-failure";
      RestartSec = "20s";
    };
  };

  systemd.services.obsidian-headless = {
    description = "Headless Obsidian with Sync";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "obsidian-openbox.service" "network-online.target" ];
    requires = [ "obsidian-openbox.service" ];
    environment = {
      DISPLAY = ":99";
      ELECTRON_DISABLE_GPU = "1";
    };
    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.obsidian}/bin/obsidian --no-sandbox";
      Restart = "on-failure";
      RestartSec = "30s";
      KillSignal = "SIGINT";
      TimeoutStopSec = "60s";
    };
  };

  systemd.services.obsidian-vnc = {
    description = "VNC server for Obsidian setup";
    # NOT in wantedBy - manually started for one-time setup
    after = [ "obsidian-xvfb.service" ];
    requires = [ "obsidian-xvfb.service" ];
    environment = {
      DISPLAY = ":99";
    };
    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.x11vnc}/bin/x11vnc -display :99 -rfbport 5900 -forever -shared";
      Restart = "on-failure";
      RestartSec = "10s";
    };
  };

  system.stateVersion = "25.11";
}
