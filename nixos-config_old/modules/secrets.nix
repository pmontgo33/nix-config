/*
Generate age key:
sudo mkdir -p /etc/sops/age
nix-shell -p age --run "age-keygen -o /etc/sops/age/keys.txt"

Create/Edit secrets file:
nix-shell -p sops --run "sops secrets/secrets.yaml"
*/

{
  imports = let
    # replace this with an actual commit id or tag
    commit = "be0eec2d27563590194a9206f551a6f73d52fa34";
  in [ 
    "${builtins.fetchTarball {
      url = "https://github.com/Mic92/sops-nix/archive/${commit}.tar.gz";
      # replace this with an actual hash
      sha256 = "1l8ri2z9qd2hn4lggr2x14rypdymbj33yg7r7sv5z9zz10g1rlip";
    }}/modules/sops"
  ];

  sops.defaultSopsFile = ../secrets/secrets.yaml;
  sops.age.keyFile = /etc/sops/age/keys.txt;
  sops.secrets.tailscale-auth-key = {};
  sops.secrets.nextcloud-admin-pass = {};
  sops.secrets.code-server-password = {};
  sops.secrets.lightning-domain = {};
}