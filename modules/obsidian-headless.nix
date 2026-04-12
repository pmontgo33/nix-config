# Obsidian Headless sync daemon using the official obsidian-headless npm CLI
# First-time setup (run once after deploy):
#   bash -c 'set -a; source /run/secrets/obsidian-env; set +a;
#     sudo -u obsidian-headless env HOME=<dataDir> ob login --email "$OBSIDIAN_EMAIL" --password "$OBSIDIAN_PASSWORD"
#     sudo -u obsidian-headless env HOME=<dataDir> ob sync-setup --path <vaultPath> --vault <vaultName> --password "$OBSIDIAN_SYNC_PASSWORD"'
# obsidian-env must contain: OBSIDIAN_EMAIL, OBSIDIAN_PASSWORD, OBSIDIAN_SYNC_PASSWORD (E2E key)

{ config, lib, pkgs, ... }:
with lib; let
  cfg = config.extra-services.obsidian-headless;
  ob = pkgs.callPackage ../packages/obsidian-headless.nix {};
in {
  options.extra-services.obsidian-headless = {
    enable = mkEnableOption "obsidian-headless sync daemon";

    vaultPath = mkOption {
      type = types.str;
      description = "Local path to the Obsidian vault to sync";
    };

    dataDir = mkOption {
      type = types.str;
      default = "/var/lib/obsidian-headless";
      description = "Home/state directory for the obsidian-headless user (stores auth session)";
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = [ ob ];

    users.users.obsidian-headless = {
      isSystemUser = true;
      group = "obsidian-headless";
      extraGroups = [ "users" ];
      home = cfg.dataDir;
    };
    users.groups.obsidian-headless = {};

    systemd.tmpfiles.rules = [
      "d  ${cfg.dataDir}  0750 obsidian-headless obsidian-headless -"
      "e  ${cfg.dataDir}  0750 obsidian-headless obsidian-headless -"
      "d  ${cfg.vaultPath} 2770 obsidian-headless obsidian-headless -"
      "e  ${cfg.vaultPath} 2770 obsidian-headless obsidian-headless -"
    ];

    sops.secrets.obsidian-env = {
      owner = "obsidian-headless";
      mode = "0400";
    };

    systemd.services.obsidian-headless = {
      description = "Obsidian Headless sync daemon";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      serviceConfig = {
        Type = "simple";
        User = "obsidian-headless";
        Group = "obsidian-headless";
        WorkingDirectory = cfg.dataDir;
        EnvironmentFile = config.sops.secrets.obsidian-env.path;
        # Log in using credentials from env file, then run continuous sync
        ExecStartPre = "${ob}/bin/ob login --email $OBSIDIAN_EMAIL --password $OBSIDIAN_PASSWORD";
        ExecStart = "${ob}/bin/ob sync --path ${cfg.vaultPath} --continuous";
        UMask = "0002";
        Restart = "on-failure";
        RestartSec = "30s";
      };
    };

    networking.firewall.allowedTCPPorts = [
      8080  # TaskNotes MCP server (Obsidian plugin)
    ];
  };
}
