# Obsidian Headless sync daemon using the official obsidian-headless npm CLI
# First-time setup per vault: ob-sync-setup <vaultName> <localPath>
# Then: systemctl start obsidian-headless-<vaultName>
# obsidian-env must contain: OBSIDIAN_EMAIL, OBSIDIAN_PASSWORD, OBSIDIAN_SYNC_PASSWORD (E2E key)

{ config, lib, pkgs, ... }:
with lib; let
  cfg = config.extra-services.obsidian-headless;
  ob = pkgs.callPackage ../packages/obsidian-headless.nix {};

  setupScript = pkgs.writeShellScriptBin "ob-sync-setup" ''
    set -euo pipefail
    VAULT_NAME="''${1:?Usage: ob-sync-setup <vaultName> <localPath>}"
    VAULT_PATH="''${2:?Usage: ob-sync-setup <vaultName> <localPath>}"
    set -a; source /run/secrets/obsidian-env; set +a
    sudo -u obsidian-headless env HOME=${cfg.dataDir} ${ob}/bin/ob sync-setup \
      --path "$VAULT_PATH" --vault "$VAULT_NAME" --password "$OBSIDIAN_SYNC_PASSWORD"
    echo "Done. Run: systemctl start obsidian-headless-''${VAULT_NAME}"
  '';

  mkVaultService = name: vault: {
    description = "Obsidian Headless sync daemon (${name})";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    startLimitIntervalSec = 600;
    startLimitBurst = 5;
    serviceConfig = {
      Type = "simple";
      User = "obsidian-headless";
      Group = "obsidian-headless";
      WorkingDirectory = cfg.dataDir;
      EnvironmentFile = config.sops.secrets.obsidian-env.path;
      # Exit 0 (not a failure) if vault hasn't been configured via ob-sync-setup yet.
      ExecStart = "${pkgs.bash}/bin/bash -c '${ob}/bin/ob sync-status --path ${vault.path} &>/dev/null || { echo \"Vault not configured — run ob-sync-setup ${name} ${vault.path}\"; exit 0; }; exec ${ob}/bin/ob sync --path ${vault.path} --continuous'";
      UMask = "0002";
      Restart = "on-failure";
      RestartSec = "60s";
    };
  };
in {
  options.extra-services.obsidian-headless = {
    enable = mkEnableOption "obsidian-headless sync daemon";

    vaults = mkOption {
      type = types.attrsOf (types.submodule {
        options.path = mkOption {
          type = types.str;
          description = "Local path where this vault will be synced";
        };
      });
      default = {};
      description = "Obsidian vaults to sync, keyed by vault name";
      example = literalExpression ''
        {
          MontyVault.path = "/var/lib/hermes/vault/MontyVault";
        }
      '';
    };

    dataDir = mkOption {
      type = types.str;
      default = "/var/lib/obsidian-headless";
      description = "Home/state directory for the obsidian-headless user (stores auth session)";
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = [ ob setupScript ];

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
    ] ++ lib.concatLists (lib.mapAttrsToList (_name: vault: [
      # Directory + existing files: setgid + group-rwx for obsidian-headless
      "d  ${vault.path} 2770 obsidian-headless obsidian-headless -"
      "e  ${vault.path} 2770 obsidian-headless obsidian-headless -"
      # Default ACL: every new file/dir inside inherits group=obsidian-headless
      # with read(+X for dirs) regardless of creator's umask. Fixes the case
      # where login.defs UMASK 077 makes fresh subprocesses write 0600 files,
      # which obsidian-headless sync daemon (in obsidian-headless group) can't read.
      "a+ ${vault.path} - - - - d:u::rwx,d:g::r-x,d:o::-"
    ]) cfg.vaults);

    sops.secrets.obsidian-env = {
      owner = "obsidian-headless";
      mode = "0400";
    };

    systemd.services = lib.mapAttrs' (name: vault:
      lib.nameValuePair "obsidian-headless-${name}" (mkVaultService name vault)
    ) cfg.vaults;

    networking.firewall.allowedTCPPorts = [
      8080  # TaskNotes MCP server (Obsidian plugin)
    ];
  };
}
