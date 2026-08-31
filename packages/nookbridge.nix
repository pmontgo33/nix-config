{ lib
, buildNpmPackage
, gnumake
, makeWrapper
, nodejs_22
, openssl
, pkg-config
, python3
, inputs
, zlib
}:

buildNpmPackage rec {
  pname = "nookbridge";
  version = "0.0.0-stage.0";

  # The source input is pinned in flake.lock to the merged Stage 5 startup
  # composition. Build against this host's pinned nixpkgs rather than using
  # NookBridge's development flake as a second package universe.
  src = inputs.nookbridge;
  nodejs = nodejs_22;

  npmDepsHash = "sha256-XGfjVhLKZwNcF5S9rogxQCljaIglfhgvHH8oRdVCx2A=";
  npmRebuildFlags = [ "--ignore-scripts" ];

  preBuild = ''
    substituteInPlace node_modules/better-sqlite3-multiple-ciphers/src/better_sqlite3.hpp \
      --replace-fail '#include <sqlite3.h>' '#include "../deps/sqlite3/sqlite3.h"'
    npm rebuild better-sqlite3-multiple-ciphers --build-from-source
  '';

  nativeBuildInputs = [
    gnumake
    makeWrapper
    nodejs_22
    pkg-config
    python3
  ];

  buildInputs = [
    openssl.dev
    zlib.dev
  ];

  dontNpmBuild = false;

  installPhase = ''
    runHook preInstall

    npm prune --omit=dev --no-save
    mkdir -p "$out/libexec/nookbridge"
    cp -r dist "$out/libexec/nookbridge/"
    cp -r node_modules "$out/libexec/nookbridge/"
    install -Dm644 package.json "$out/libexec/nookbridge/package.json"
    install -Dm644 LICENSE "$out/share/licenses/nookbridge/LICENSE"

    makeWrapper ${nodejs_22}/bin/node "$out/bin/nookd" \
      --add-flags "$out/libexec/nookbridge/dist/nookd.js"

    runHook postInstall
  '';

  meta = {
    description = "NookBridge Notesnook read-only Unix-socket service";
    homepage = "https://git.montycasa.net/patrick/NookBridge";
    license = lib.licenses.gpl3Plus;
    mainProgram = "nookd";
    platforms = lib.platforms.linux;
  };
}
