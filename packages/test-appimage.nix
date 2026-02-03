{ lib,
  fetchurl,
  appimageTools,
}:

let
  version = "1.4.0";
  pname = "quba-test";

  src = fetchurl {
    url = "https://github.com/ZUGFeRD/quba-viewer/releases/download/v${version}/Quba-${version}.AppImage";
    hash = "sha256-EsTF7W1np5qbQQh3pdqsFe32olvGK3AowGWjqHPEfoM=";
  };
in
appimageTools.wrapType2 {
  inherit pname version src;

  meta = with lib; {
    description = "Test AppImage package";
    homepage = "https://github.com/ZUGFeRD/quba-viewer";
    license = licenses.asl20;
    platforms = platforms.linux;
    mainProgram = pname;
  };
}
