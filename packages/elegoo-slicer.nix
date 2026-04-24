{ lib
, fetchurl
, appimageTools
, writeText
, dejavu_fonts
, noto-fonts
, source-han-sans
, runCommand
, fontconfig
}:

let
  pname = "elegoo-slicer";
  version = "1.5.0.7";

  src = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v${version}/ElegooSlicer_Linux_V${version}.AppImage";
    hash = "sha256-9YjuwP2OAGRFfqeKvykRAdSP0ql311Iui3qBrcAOm20=";
  };

  appimageContents = appimageTools.extractType1 {
    inherit pname version src;
  };

  # Font bundle placed at share/fonts so the FHS env merges it into
  # /usr/share/fonts, where WebKit's subprocess can find it.
  webFonts = runCommand "elegoo-slicer-web-fonts" {} ''
    mkdir -p $out/share/fonts
    ln -s ${dejavu_fonts}/share/fonts $out/share/fonts/dejavu
    ln -s ${noto-fonts}/share/fonts $out/share/fonts/noto
    ln -s ${source-han-sans}/share/fonts $out/share/fonts/source-han-sans
  '';

  # Self-contained fontconfig that does NOT include /etc/fonts/conf.d or use
  # the user's ~/.cache/fontconfig. This avoids a SIGSEGV in Pango's
  # ensure_faces triggered by the NixOS host fontconfig inside the bwrap sandbox.
  fontsConf = writeText "elegoo-slicer-fonts.conf" ''
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
    <fontconfig>
      <dir>${dejavu_fonts}/share/fonts</dir>
      <dir>${noto-fonts}/share/fonts</dir>
      <dir>${source-han-sans}/share/fonts</dir>
      <dir>/usr/share/fonts</dir>
      <cachedir prefix="xdg">elegoo-slicer-fontconfig-cache</cachedir>
      <!-- The WebKit UI requests PingFang SC / Microsoft YaHei / sans-serif.
           Map all of these to Source Han Sans which covers CJK + Latin. -->
      <alias><family>PingFang SC</family><prefer><family>Source Han Sans</family></prefer></alias>
      <alias><family>Microsoft YaHei</family><prefer><family>Source Han Sans</family></prefer></alias>
      <alias><family>微软雅黑</family><prefer><family>Source Han Sans</family></prefer></alias>
      <alias><family>sans-serif</family><prefer><family>Source Han Sans</family></prefer></alias>
    </fontconfig>
  '';
in
appimageTools.wrapType1 rec {
  inherit pname version src;

  extraPkgs = pkgs: with pkgs; [
    webFonts
    webkitgtk_4_1
    bzip2.out
    zstd
    zlib
    libsoup_3
    libmspack
    # Network libraries for printer connectivity
    curl
    openssl
    nss
    nspr
    avahi
    # Additional system libraries
    systemd
    dbus
    glib
    glibc
  ];

  # Point fontconfig at our self-contained config instead of the host NixOS one,
  # which triggers SIGSEGV in Pango's ensure_faces when opening GTK file dialogs.
  # Also add /usr/lib64 to LD_LIBRARY_PATH because the AppImage binary has a
  # hardcoded RUNPATH pointing to the developer's build machine and falls through
  # to system paths, which on NixOS FHS env puts 64-bit libs in /usr/lib64 only.
  extraPreBwrapCmds = ''
    export FONTCONFIG_FILE=${fontsConf}
    export FONTCONFIG_SYSROOT=""
    export LD_LIBRARY_PATH="/usr/lib64:/usr/lib:$LD_LIBRARY_PATH"
  '';


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
