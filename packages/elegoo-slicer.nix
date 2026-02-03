{ lib
, fetchurl
, appimageTools
}:

let
  pname = "elegoo-slicer";
  version = "1.3.0.11";

  src = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v${version}/ElegooSlicer_Linux_Ubuntu2404_V${version}.AppImage";
    hash = "sha256-XDy1gcwQFZjo1YEnBAUDHtscoJHjkZJdugbJ3HpzYMg=";
  };

  appimageContents = appimageTools.extractType1 {
    inherit pname version src;
  };
in
appimageTools.wrapType1 rec {
  inherit pname version src;

  # Minimal sandboxing - share network and key system directories
  extraBwrapArgs = [
    "--share-net"
    "--filesystem=host"
  ];

  extraPkgs = pkgs: with pkgs; [
    webkitgtk_4_1
    # Network libraries for printer connectivity
    curl
    openssl
    nss
    nspr
    avahi
    nss-mdns
    # Additional system libraries
    systemd
    dbus
    glib
    glibc
  ];

  extraInstallCommands = ''
    install -Dm444 ${appimageContents}/ElegooSlicer.desktop $out/share/applications/elegoo-slicer.desktop
    install -Dm444 ${appimageContents}/ElegooSlicer.png $out/share/pixmaps/ElegooSlicer.png
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
