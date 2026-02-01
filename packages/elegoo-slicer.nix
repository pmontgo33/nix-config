{ lib
, fetchurl
, appimageTools
}:

let
  pname = "elegoo-slicer";
  version = "1.3.0.11";

  src = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v${version}/ElegooSlicer_Linux_Ubuntu2404_V${version}.AppImage";
    hash = "sha256-5c3cb581cc101598e8d581270405031edb1ca091e391925dba06c9dc7a7360c8";
  };

  appimageContents = appimageTools.extractType2 {
    inherit pname version src;
  };

in appimageTools.wrapType2 {
  inherit pname version src;

  extraPkgs = pkgs: [ pkgs.webkitgtk_4_1 ];

  extraInstallCommands = ''
    # Install desktop file
    install -Dm644 ${appimageContents}/ElegooSlicer.desktop $out/share/applications/elegoo-slicer.desktop

    # Install icon
    install -Dm644 ${appimageContents}/ElegooSlicer.png $out/share/pixmaps/elegoo-slicer.png

    # Fix desktop file to point to the correct executable
    substituteInPlace $out/share/applications/elegoo-slicer.desktop \
      --replace-fail 'Exec=AppRun' 'Exec=${pname}'
  '';

  meta = with lib; {
    description = "Open-source slicer compatible with most FDM printers";
    homepage = "https://github.com/ELEGOO-3D/ElegooSlicer";
    license = licenses.agpl3Only;
    platforms = platforms.linux;
    mainProgram = pname;
  };
}
