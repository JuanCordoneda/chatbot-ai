#!/bin/sh
# Genera las imágenes de cabecera de la plantilla de WhatsApp (1200×630) a partir de midi-10.html y midi-15.html.
# Después copialas a openAIService/midi/ para que el servicio las publique.
set -e
cd "$(dirname "$0")"
tmp=$(mktemp -d)
for n in 10 15; do
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
    --force-device-scale-factor=1 --window-size=1200,630 --virtual-time-budget=5000 \
    --user-data-dir="$tmp/chrome" --screenshot="$tmp/midi-$n.png" "file://$PWD/midi-$n.html" >/dev/null 2>&1
  magick "$tmp/midi-$n.png" -strip -interlace JPEG -sampling-factor 4:2:0 -quality 84 "toloache-midi-$n.jpg"
  echo "toloache-midi-$n.jpg — $(($(stat -f%z "toloache-midi-$n.jpg") / 1024)) KB"
done
rm -rf "$tmp"
