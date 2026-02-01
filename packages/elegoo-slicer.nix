{ stdenv
, fetchurl
}:

stdenv.mkDerivation rec {
  pname = "elegoo-slicer";
  version = "1.3.0.11";

  src = fetchurl {
    url = "https://github.com/ELEGOO-3D/ElegooSlicer/releases/download/v${version}/ElegooSlicer_Linux_Ubuntu2404_V${version}.AppImage";
    hash = "sha256-5c3cb581cc101598e8d581270405031edb1ca091e391925dba06c9dc7a7360c8";
  };

  dontUnpack = true;
  dontBuild = true;

  installPhase = ''
    mkdir -p $out/bin
    cp ${src} $out/bin/elegoo-slicer
    chmod +x $out/bin/elegoo-slicer
  '';
}
