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
      "pbs-password" = {
#         mode = "0400";
#         owner = "root";
#         group = "root";
      };
      "pbs-fingerprint" = {
#         mode = "0400";
#         owner = "root";
#         group = "root";
      };

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
