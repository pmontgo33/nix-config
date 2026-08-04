{ lib
, python312Packages
, fetchurl
}:

# Hermes-Relay Python wheel (server-v1.5.0). Vendored into nix-config so
# the host can run `python -m plugin.relay --no-ssl` and pair Android /
# desktop clients over a Tailscale-only relay listener.
#
# aiohttp note: the wheel's METADATA declares aiohttp>=3.14.1,<4.
# The pinned nixpkgs (nixos-26.05) ships aiohttp 3.13.5. The runtime-deps
# check fails as a result, so we relax it. Luna xhigh review (2026-08-03)
# confirmed that imports + runtime work fine with 3.13.5 for the relay
# surfaces we currently need (server, QR pairing, status). If/when relay
# voice or realtime-agent features land, re-evaluate by bumping
# nixpkgs-unstable's aiohttp (3.14.1) into the flake and dropping the
# relax — see Hermes/Plans/active/hermes-relay-nix-installation.md.
python312Packages.buildPythonPackage rec {
  pname = "hermes-relay";
  version = "1.5.0";
  format = "wheel";

  src = fetchurl {
    url = "https://github.com/Codename-11/hermes-relay/releases/download/server-v${version}/hermes_relay-${version}-py3-none-any.whl";
    hash = "sha256-bE/qmwLKUZ6xLPr5Y0ACUz5bTa3s0u63UtM3rpsT0Mw";
  };

  propagatedBuildInputs = with python312Packages; [
    requests
    aiohttp
    segno
    pyyaml
    httpx
    websocket-client
  ];

  # See aiohttp note above.
  pythonRelaxDeps = [ "aiohttp" ];

  # Wheel has no test suite; running one would require aiohttp test deps.
  doCheck = false;

  meta = {
    description = "Hermes-Relay server: WSS relay for Android/desktop power tools, QR pairing CLI, and plugin runtime";
    homepage = "https://github.com/Codename-11/hermes-relay";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "plugin.relay";
  };
}