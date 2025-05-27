nrs host="$HOSTNAME":
  sudo nixos-rebuild switch --flake /home/patrick/nix-config#{{host}}

nrs-r host:
  sudo nixos-rebuild switch --flake /home/patrick/nix-config#{{host}} --target-host root@{{host}} --use-remote-sudo

nrs-wtf host="$HOSTNAME":
  sudo nixos-rebuild switch --flake /home/patrick/nix-config#{{host}} --show-trace --print-build-logs --verbose

nfc:
  sudo nix flake check

agenix file:
  cd secrets && nix run github:ryantm/agenix -- -e {{file}}

agenix-rekey:
  cd secrets && nix run github:ryantm/agenix -- -r

secrets:
  -nix-shell -p sops --run "SOPS_AGE_KEY_FILE='/etc/sops/age/keys.txt' sops secrets/secrets.yaml"

git-acpush message:
  git add .
  git commit -m "{{message}}"
  git push origin master

git-cpush message:
  git commit -m "{{message}}"
  git push origin master

git-rpull remote:
  ssh root@{{remote}} "cd /etc/nixos && git pull https://github.com/pmontgo33/nixos-config.git"

# this is a comment
another-recipe:
  @echo 'This is another recipe.'
