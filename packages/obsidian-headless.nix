{ lib, buildNpmPackage, fetchurl }:

buildNpmPackage rec {
  pname = "obsidian-headless";
  version = "0.0.8";

  src = fetchurl {
    url = "https://registry.npmjs.org/obsidian-headless/-/obsidian-headless-${version}.tgz";
    hash = "sha256-+fg6tr69/7n73KhlJxAb4ujMOvH64hLwIt/6MeAiNtU=";
  };

  # No lock file in the tarball — patch one in that we generated
  postPatch = ''
    cp ${./obsidian-headless-lock.json} package-lock.json
  '';

  npmDepsHash = "sha256-+ZBZifTz5liTuu2Evf64iAPgOZvOBCHkbPERKw2T2aQ=";

  # cli.js is the entry point; no build step needed
  dontNpmBuild = true;

  meta = {
    description = "Headless CLI client for Obsidian Sync";
    homepage = "https://obsidian.md/help/headless";
    license = lib.licenses.unfree;
    mainProgram = "ob";
    platforms = lib.platforms.linux;
  };
}
