{ stdenv
, fetchurl
, writeShellScript
, nix
, webkitgtk_4_1
}:

let
  appimage = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v1.3.0.11/ElegooSlicer_Linux_Ubuntu2404_V1.3.0.11.AppImage";
    hash = "sha256-5c3cb581cc101598e8d581270405031edb1ca091e391925dba06c9dc7a7360c8";
  };

  wrapper = writeShellScript "elegoo-slicer-wrapper" ''
    ${nix}/bin/nix-shell -p 'appimage-run.override { extraPkgs = pkgs: [ pkgs.webkitgtk_4_1 ]; }' --run "appimage-run ${appimage} $*"
  '';
in

stdenv.mkDerivation {
  pname = "elegoo-slicer";
  version = "1.3.0.11";

  dontUnpack = true;
  dontBuild = true;

  installPhase = ''
    mkdir -p $out/bin
    cp ${wrapper} $out/bin/elegoo-slicer
    chmod +x $out/bin/elegoo-slicer
  '';
}
