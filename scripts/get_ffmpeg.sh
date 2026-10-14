#!/usr/bin/env bash
# Descarga un ffmpeg estático con libass (necesario para subtítulos) a ./bin/
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p bin

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS-$ARCH" in
  Darwin-arm64)  URL="https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip" ;;
  Darwin-x86_64) URL="https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffmpeg.zip" ;;
  Linux-x86_64)  URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" ;;
  Linux-aarch64) URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz" ;;
  *) echo "Plataforma no soportada: $OS-$ARCH. Instala ffmpeg con libass manualmente."; exit 1 ;;
esac

echo "Descargando ffmpeg estático para ${OS}-${ARCH}…"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/x"
if [[ "$URL" == *.zip ]]; then
  curl -fL --progress-bar -o "$TMP/ffmpeg.zip" "$URL"
  unzip -o -q "$TMP/ffmpeg.zip" -d "$TMP/x"
else
  curl -fL --progress-bar -o "$TMP/ffmpeg.tar.xz" "$URL"
  tar -xf "$TMP/ffmpeg.tar.xz" -C "$TMP/x"
fi
# el binario puede venir en la raíz o en una subcarpeta según el build
BIN="$(find "$TMP/x" -type f -name ffmpeg | head -n 1)"
if [[ -z "$BIN" ]]; then
  echo "El archivo descargado no contiene ffmpeg"; exit 1
fi
mv "$BIN" bin/ffmpeg

chmod +x bin/ffmpeg
echo "Listo: $(./bin/ffmpeg -version | head -1)"
./bin/ffmpeg -hide_banner -filters 2>/dev/null | grep -qE '\bass\b' \
  && echo "✓ libass disponible (subtítulos OK)" \
  || echo "⚠ este build no trae libass"
