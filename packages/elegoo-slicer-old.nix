{ lib
, fetchurl
, appimageTools
}:

appimageTools.wrapType2 {
  pname = "elegoo-slicer";
  version = "1.3.0.11";

  src = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v1.3.0.11/ElegooSlicer_Linux_Ubuntu2404_V1.3.0.11.AppImage";
    hash = "sha256-5c3cb581cc101598e8d581270405031edb1ca091e391925dba06c9dc7a7360c8";
  };

  extraPkgs = pkgs: [
    pkgs.webkitgtk_4_1
  ];

  meta = with lib; {
    description = "Open-source slicer compatible with most FDM printers";
    homepage = "https://github.com/ELEGOO-3D/ElegooSlicer";
    license = licenses.agpl3Only;
    platforms = platforms.linux;
  };
}
