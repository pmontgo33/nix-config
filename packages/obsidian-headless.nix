{ lib, buildNpmPackage, fetchurl }:

buildNpmPackage rec {
  pname = "obsidian-headless";
  version = "0.0.13";

  src = fetchurl {
    url = "https://registry.npmjs.org/obsidian-headless/-/obsidian-headless-${version}.tgz";
    hash = "sha256-m44a05F6ZdU8WrdNBqz17I2UHjsCvZvV0DXWgA5TMZg=";
  };

  postPatch = ''
    cp ${./obsidian-headless-lock.json} package-lock.json
  '';

  npmDepsHash = "sha256-9CrEHMWe3fhqk6gK/jtU01RaZWVxcKEG5ItRgIYO0FQ=";

  dontNpmBuild = true;

  meta = {
    description = "Headless CLI client for Obsidian Sync";
    homepage = "https://obsidian.md/help/headless";
    license = lib.licenses.unfree;
    mainProgram = "ob";
    platforms = lib.platforms.linux;
  };
}
