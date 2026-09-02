#!/usr/bin/env bash
# Renueva la sesión de Instagram de producción.
#
# Saca las cookies del Safari de esta Mac (donde está la sesión abierta que no
# cerramos nunca), las sube a Railway y espera a que el servicio vuelva a
# levantar para confirmar que quedó viva. El server NO renueva la cookie solo:
# lo que sube acá es una foto del día, y cuando Instagram la invalida los posts
# empiezan a fallar con "no se pudo obtener el contenido del post".
#
# Requiere:
#   - Terminal con Acceso Total al Disco (si no, no puede leer las cookies de
#     Safari: Ajustes → Privacidad y seguridad → Acceso total al disco).
#   - railway CLI logueado y el proyecto linkeado (railway status).
#
# Uso:
#   ./script_COOKIE.sh              renueva y verifica
#   ./script_COOKIE.sh --check      solo dice si la sesión de prod está viva
#   ./script_COOKIE.sh --manual     pegás las cookies a mano (sin permiso de disco)
#
# Si Instagram trabó la cuenta (checkpoint), renovar la cookie NO alcanza: hay
# que entrar con esa cuenta al navegador, confirmar que es ella, y recién ahí
# volver a sacar la sesión.

set -euo pipefail

REPO="/Users/abc/Desktop/CROW/crowi"
VENV="$REPO/.venv-ig"
SERVICIO="openai"
HEALTH="https://openai-production-531a.up.railway.app/health/instagram"
# Post público y estable: sirve de sonda, no tiene nada que ver con los clientes.
SHORTCODE="${TEST_SHORTCODE:-DY5mFTuxsIO}"

cd "$REPO"

esta_viva() {
  curl -s -m 20 "$HEALTH?shortcode=$SHORTCODE" | grep -q '"ok":true'
}

if [ "${1:-}" = "--check" ]; then
  if esta_viva; then
    echo "$(date '+%F %H:%M') sesión viva"
    exit 0
  fi
  echo "$(date '+%F %H:%M') sesión CAÍDA — corré ./script_COOKIE.sh para renovarla"
  # Corriendo por cron nadie mira la salida: el aviso tiene que aparecer en la
  # pantalla. La idea es enterarse antes que el vendedor, no después.
  osascript -e 'display notification "Los posts no se van a poder generar. Corré ./script_COOKIE.sh" with title "Growi: sesión de Instagram caída" sound name "Basso"' 2>/dev/null || true
  exit 1
fi

# El venv vive en el repo (gitignoreado) para que browser-cookie3 no ensucie el
# Python del sistema, que además está protegido por PEP 668.
if [ ! -x "$VENV/bin/python" ]; then
  echo "→ creando venv en $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install browser-cookie3
fi

# La cookie se arma PRIMERO y se revisa antes de subir nada: con un pipe directo,
# si la extracción fallaba igual se le mandaba a Railway un valor vacío y el
# servicio arrancaba sin sesión ("Empty value provided via stdin").
echo "→ leyendo la sesión de Safari"
COOKIE=""
if [ "${1:-}" = "--manual" ]; then
  # Sin Acceso Total al Disco: las cookies las pegás vos desde el Inspector Web
  # de Safari (Desarrollo → Almacenamiento → Cookies → instagram.com).
  COOKIE="$(python3 scripts/set_ig_cookies.py --stdout --no-env)"
else
  COOKIE="$("$VENV/bin/python" scripts/ig_cookies_from_browser.py \
              --browser safari --stdout --no-env || true)"
fi

if ! echo "$COOKIE" | grep -q '"sessionid"'; then
  echo
  echo "no pude sacar la sesión de Safari, así que NO subo nada."
  echo "Si el error fue 'Operation not permitted', a Terminal le falta Acceso Total"
  echo "al Disco (Ajustes → Privacidad y seguridad → Acceso total al disco → +"
  echo "Terminal, y después ⌘Q y abrirla de nuevo)."
  echo "O sacá las cookies a mano del Inspector Web: ./script_COOKIE.sh --manual"
  exit 1
fi

echo "→ subiendo a Railway ($SERVICIO)"
printf '%s' "$COOKIE" \
  | railway variables --set-from-stdin INSTAGRAM_COOKIES_JSON --service "$SERVICIO"

echo "→ Railway está redeployando; espero a que la sesión responda"
for i in $(seq 1 40); do
  if esta_viva; then echo "listo: la sesión de prod está viva"; exit 0; fi
  sleep 15
done

echo "la variable se subió pero el health sigue fallando después de 10 minutos."
echo "Mirá: railway logs -s $SERVICIO | grep ig_api"
exit 1
