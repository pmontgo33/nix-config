{inputs, config, lib, ...}:

{
# Enable sops
  sops = {
    defaultSopsFile = ./secrets.yaml;
    defaultSopsFormat = "yaml";

    # Specify the age key file location
    age.keyFile = "/home/patrick/.config/sops/age/keys.txt";

    # Define secrets
    secrets = {
      "tailscale_auth_key" = {};
      "pbs-password" = {};
      "pbs-fingerprint" = {};

#       "user-password" = { neededForUsers = true; };
#       "wifi-password" = {};
#       "database-password" = {
#         owner = "postgres";
#         group = "postgres";
#         mode = "0440";
#       };
    };
  };
}
