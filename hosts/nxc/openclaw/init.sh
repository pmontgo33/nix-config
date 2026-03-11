#!/bin/sh
# Bootstrap Python packages
if ! python3 -c "import requests" 2>/dev/null; then
  python3 -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')"
  python3 /tmp/get-pip.py --break-system-packages --quiet
  python3 -m pip install --break-system-packages --quiet requests
fi

# rsync is bind-mounted from the NixOS host (see configuration.nix)

exec docker-entrypoint.sh "$@"
