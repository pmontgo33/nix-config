{ stdenv
, fetchurl
, appimage-run
, makeWrapper
, webkitgtk_4_1
}:

stdenv.mkDerivation rec {
  pname = "elegoo-slicer";
  version = "1.3.0.11";

  src = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v${version}/ElegooSlicer_Linux_Ubuntu2404_V${version}.AppImage";
    hash = "sha256-5c3cb581cc101598e8d581270405031edb1ca091e391925dba06c9dc7a7360c8";
  };

  nativeBuildInputs = [ makeWrapper ];

  dontUnpack = true;
  dontBuild = true;

  installPhase =
    let
      appimage-run-webkit = appimage-run.override {
        extraPkgs = p: [ p.webkitgtk_4_1 ];
      };
    in
    ''
      mkdir -p $out/bin
      install -m755 ${src} $out/bin/elegoo-slicer.AppImage

      makeWrapper ${appimage-run-webkit}/bin/appimage-run $out/bin/elegoo-slicer \
        --add-flags "$out/bin/elegoo-slicer.AppImage"
    '';
}
