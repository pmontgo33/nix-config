{ config, pkgs, ... }:

let
  nix-bitcoin = builtins.fetchTarball {
    url = "https://github.com/fort-nix/nix-bitcoin/releases/download/v0.0.119/nix-bitcoin-0.0.119.tar.gz";
    sha256 = "1q78ajffi79jl4nml02j2g6aa283xg1p44kpnq7fd78by62axwzk";
  };
  # Your custom domain
  domain = config.sops.secrets.bitcoin-domain.path;
in
{
  imports = [
    "${nix-bitcoin}/modules/modules.nix"
    ../base/base_config.nix
    ../modules/tailscale.nix
  ];

  # Automatically generate all secrets required by services.
  # The secrets are stored in /etc/nix-bitcoin-secrets
  nix-bitcoin.generateSecrets = true;
  
  # Enable Bitcoin Core (if running full node)
  # services.bitcoind.enable = true;
  # services.bitcoind.network = "main";
  # services.bitcoind.dataDir = "/var/lib/bitcoind";

  # Disable Bitcoin Core - we'll use neutrino mode instead
  #services.bitcoind.enable = false;
  
  # Enable LND with custom domain settings
  services.lnd = {
    enable = true;
    dataDir = "/var/lib/lnd";
    
    extraConfig = ''
      [Application Options]
      debuglevel=info
      tlsextraip=0.0.0.0
      tlsextradomain=lnd.${domain}
      externalip=lnd.${domain}
      
      [Bitcoin]
      bitcoin.active=1
      bitcoin.mainnet=1
      bitcoin.node=neutrino

      [neutrino]
      neutrino.connect=faucet.lightning.community
      neutrino.connect=lnd.bitrefill.com
      neutrino.connect=btcd-mainnet.lightning.computer
      neutrino.feeurl=https://nodes.lightning.computer/fees/v1/btc-fee-estimates.json
      
      [tor]
      tor.active=true
      tor.v3=true
    '';
  };

  # Enable Tor for privacy
  services.tor.enable = true;
  
  # Optional: Enable Lightning web interface
  services.rtl.enable = true;
  services.rtl.port = 3000;
  
  # Networking: Open required ports
  networking.firewall.allowedTCPPorts = [ 9735 3000 ]; # [ 80 443 8333 9735 3000 3002 ];
  
  # Storage configuration remains the same
  # fileSystems."/var/lib/bitcoind" = {
  #   device = "/dev/disk/by-label/bitcoin";
  #   fsType = "ext4";
  #   options = [ "noatime" ];
  # };
  
  fileSystems."/var/lib/lnd" = {
    device = "/dev/disk/by-label/lightning";
    fsType = "ext4";
    options = [ "noatime" ];
  };
}