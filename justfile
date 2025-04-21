nrs host="$HOSTNAME":
  sudo nixos-rebuild switch --flake /home/patrick/nix-config#{{host}}

nfc:
  nix flake check

agenix file:
  cd secrets && nix run github:ryantm/agenix -- -e {{file}}

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
