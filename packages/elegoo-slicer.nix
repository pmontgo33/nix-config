{ writeShellScriptBin
, fetchurl
, appimage-run
}:

let
  pname = "elegoo-slicer";
  version = "1.3.0.11";

  src = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v${version}/ElegooSlicer_Linux_Ubuntu2404_V${version}.AppImage";
    hash = "sha256-5c3cb581cc101598e8d581270405031edb1ca091e391925dba06c9dc7a7360c8";
  };

  # Override appimage-run to include webkitgtk_4_1
  appimage-run-webkit = appimage-run.override {
    extraPkgs = pkgs: [ pkgs.webkitgtk_4_1 ];
  };
in

writeShellScriptBin pname ''
  exec ${appimage-run-webkit}/bin/appimage-run ${src} "$@"
''
