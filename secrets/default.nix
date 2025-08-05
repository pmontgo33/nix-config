{inputs, config, lib, ...}:

{
# Enable sops
  sops = {
    defaultSopsFile = ./secrets.yaml;
    defaultSopsFormat = "yaml";

    # Specify the age key file location
    age.keyFile = "/etc/sops/age/keys.txt";
  };
}
