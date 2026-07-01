# Common configuration for all hosts

{ config, lib, pkgs, inputs, outputs, ... }: {

  imports = [
      ../../modules
      ../../secrets
  ];

  extra-services.auto-upgrade.enable = lib.mkDefault true;

  environment.systemPackages = with pkgs; [
    wget
    git
    vim
    just
    fail2ban
    cachix
  ];

  # Set Timezone
  time.timeZone = "America/New_York";

  # Set the host platform (replaces deprecated 'system' parameter)
  nixpkgs.hostPlatform = "x86_64-linux";

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # Enable the Flakes feature and the accompanying new nix command-line tool
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Cachix binary cache — fills from cache.nixos.org first, then this.
  # Push access requires a Cachix auth token at ~/.config/cachix/token on the builder.
  nix.settings.substituters = [ "https://monty-nix-config.cachix.org" ];
  nix.settings.trusted-public-keys = [
    "monty-nix-config.cachix.org-1:DYj9HWPgt1EBfsSOc1JEbqFGM5xAN0ZcykEmzb8uny4="
  ];

  # Add public keys for management machines
  users.users.root = {
    hashedPassword = lib.mkForce "$6$fu.ra7ConU15mC8P$kMM7PcKtpo3ruRpqncC47lbRKYK3/f2z4shsK8pewbxohu6OjpxdJP/NYLLvEg4NjN29BSt3zPq6UwSxK1Zn90";
    initialHashedPassword = lib.mkForce null;
    openssh.authorizedKeys.keys = [
        # Management public keys
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEr9aBBJ73I/tXOT00krxHglmAqZ0A8xt7Hk5s2zMwCo patrick@hp-nixos"
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE+WBmDc0ACtIS4DZl2fHyFCxxAMIa6c5PuMgvuSBD5R patrick@nix-fury"
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMa5HxhMxXea3SH+hxZbr0XAxenGnl42GgQTzdXNbQSW Pixel 9"
      ];
    shell = pkgs.fish;
  };

  programs.fish.enable = true;

  system.activationScripts.copyJustfile = ''
    cp -f ${./.dotfiles/justfile} /root/justfile
    chmod 644 /root/justfile
  '';

  # Cachix auth token — mounted from sops secrets and templated into
  # /root/.config/cachix/cachix.dhall at activation time so cachix CLI
  # works out-of-the-box. Root-owned since not every host has a
  # 'patrick' user (e.g. LXCs running dedicated services).
  sops.secrets."cachix-auth-token" = {
    mode = "0600";
    owner = "root";
    group = "root";
  };

  # Run as a systemd service (not activation script) so it executes AFTER
  # sops-install-secrets mounts /run/secrets/cachix-auth-token. Avoids the
  # race where the activation script fires before sops has populated the
  # secret directory.
  systemd.services.cachix-config = {
    description = "Template Cachix auth token";
    wantedBy = [ "multi-user.target" "sops-install-secrets.service" ];
    after = [ "sops-install-secrets.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "root";
    };
    script = ''
      mkdir -p /root/.config/cachix
      cat > /root/.config/cachix/cachix.dhall <<EOF
{ authToken = "$(cat ${config.sops.secrets."cachix-auth-token".path})"
, hostname = "https://cachix.org"
, binaryCaches = [] : List { name : Text, secretKey : Text }
}
EOF
      chmod 0600 /root/.config/cachix/cachix.dhall
    '';
  };

  # Automatic Garbage Collection
  programs.nh = {
    enable = true;
    clean = {
      enable = true;
      dates = "weekly";
      extraArgs = "--keep-since 14d --keep 5";
    };
    flake = "github:pmontgo33/nix-config";
  };

}
