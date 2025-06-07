{ config, ... }:
{
  age.secrets = {
    tailscale_auth_key.file = ../../secrets/tailscale_auth_key.age;
    syncthing_password.file = ../../secrets/syncthing_password.age;
  };

  system.activationScripts.processSecret = {
    text = ''
      SYNCTHING_PASSWORD=$(cat ${config.age.secrets.syncthing_password.path})
    '';
    deps = [ "agenix" ];  # Ensure agenix runs first
  };
}
