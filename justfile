nrs host="$HOSTNAME":
  sudo nixos-rebuild switch --flake /home/patrick/nix-config#{{host}}

nrs-r host:
  sudo nixos-rebuild switch --flake /home/patrick/nix-config#{{host}} --target-host root@{{host}}

nrb-r host:
  sudo nixos-rebuild boot --flake /home/patrick/nix-config#{{host}} --target-host root@{{host}}


nrsb-r host:
  nixos-rebuild switch --flake /home/patrick/nix-config#{{host}} --build-host root@nix-fury --target-host root@{{host}}

nrs-wtf host="$HOSTNAME":
  sudo nixos-rebuild switch --flake /home/patrick/nix-config#{{host}} --show-trace --print-build-logs --verbose

nfc:
  sudo nix flake check

agenix file:
  cd secrets && nix run github:ryantm/agenix -- -e {{file}}

agenix-rekey:
  cd secrets && nix run github:ryantm/agenix -- -r

secrets:
  -nix-shell -p sops --run "SOPS_AGE_KEY_FILE='/home/patrick/.config/sops/age/keys.txt' sops secrets/secrets.yaml"

git-acpush message branch="master":
  git add .
  git commit -m "{{message}}"
  git push origin "{{branch}}"

git-cpush message branch="master":
  git commit -m "{{message}}"
  git push origin "{{branch}}"

git-rpull remote:
  ssh root@{{remote}} "cd /etc/nixos && git pull https://github.com/pmontgo33/nixos-config.git"

ap host tags="all" vars="":
  ansible-playbook ansible/playbooks/host_{{host}}.yml --tags "{{tags}}" -e "{{vars}}"

ap-bootstrap host:
  ansible-playbook ansible/playbooks/bootstrap_new_host.yml -e "host={{host}}"

av-edit-vars:
  ansible-vault edit ansible/playbooks/vars/homelab_secret_vars.yml

reqs:
  ansible-galaxy install -r ansible/requirements.yml
