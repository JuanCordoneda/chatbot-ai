#!/usr/bin/env bash
# Renueva la sesión de Instagram de producción DESDE ESTA MAC.
#
# OJO: este ya no es el camino principal. La forma normal de renovar es el panel
# (/admin → pestaña Instagram): se pegan las cookies desde cualquier navegador,
# incluso del celular, se prueban antes de guardarse y no hace falta ni Railway
# ni permisos de disco. Este script quedó para dos casos: cargar la cuenta
# cuando el panel todavía no tiene ninguna, y como salida de emergencia si el
# webService está caído.
#
# Lo que sube acá es la variable INSTAGRAM_COOKIES_JSON, que hoy es el ÚLTIMO
# recurso: el scraper primero usa las cuentas cargadas en el panel.
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
#   ./script_COOKIE.sh              renueva y verifica (lee Safari)
#   ./script_COOKIE.sh --chrome     idem, pero lee Chrome
#   ./script_COOKIE.sh --chrome --profile "Juan Cruz"   un perfil concreto
#   ./script_COOKIE.sh --chrome --perfiles              qué perfiles hay
#   ./script_COOKIE.sh --check      solo dice si la sesión de prod está viva
#   ./script_COOKIE.sh --manual     pegás las cookies a mano (sin permiso de disco)
#
# OJO con Chrome: en macOS las cookies están cifradas con una clave del Llavero,
# así que salta un diálogo pidiendo permiso. Y hay que decirle el PERFIL: sin
# eso browser-cookie3 se queda con el primero que encuentra, que en esta Mac es
# el que tiene la sesión vieja de @crowagency.ofc.
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

# De qué navegador (y perfil) salen las cookies. Empezó hardcodeado en Safari,
# que era donde vivía la única cuenta; con varias, cada una va en un navegador o
# perfil distinto y hay que poder elegir. Sin --profile, browser-cookie3 se
# queda con el primer perfil que encuentra: no ignora los otros, elige uno sin
# avisar, y así se sube a producción la cuenta equivocada.
NAVEGADOR="${IG_BROWSER:-safari}"
PERFIL="${IG_PROFILE:-}"
MODO=""
EXTRA=""
# Se recorren TODOS los flags y no solo el primero: los envoltorios por
# navegador llaman con "--chrome --check", y mirando solo $1 el --check se
# perdía en silencio — pedías un chequeo y te renovaba la sesión.
while [ $# -gt 0 ]; do
  case "$1" in
    --safari|--chrome|--firefox|--brave|--edge|--chromium|--opera)
      NAVEGADOR="${1#--}" ;;
    --browser)
      NAVEGADOR="${2:-}"; shift
      [ -n "$NAVEGADOR" ] || { echo "uso: --browser <safari|chrome|firefox|...>"; exit 1; } ;;
    --profile)
      PERFIL="${2:-}"; shift
      [ -n "$PERFIL" ] || { echo "uso: --profile \"Profile 15\" (o el nombre: \"Juan Cruz\")"; exit 1; } ;;
    --check|--manual|--perfiles)
      MODO="$1" ;;
    --force)
      EXTRA="--force" ;;   # lo entiende ig_cookies_from_browser.py, no este script
    *)
      echo "no conozco la opción '$1'."
      echo "uso: ./script_COOKIE.sh [--safari|--chrome|--browser <nav>] [--profile <perfil>]"
      echo "                        [--check|--manual|--perfiles] [--force]"
      exit 1 ;;
  esac
  shift
done

cd "$REPO"

# El endpoint devuelve lo último que sabe el monitor del server, sin molestar a
# Instagram. Es lo que hay que mirar para "¿está viva?".
esta_viva() {
  curl -s -m 20 "$HEALTH?shortcode=$SHORTCODE" | grep -q '"ok": *true'
}

# Y esto pregunta DE VERDAD. Se usa una sola vez, para confirmar que la cookie
# que acabamos de subir sirve: si se usara en cada vuelta de la espera serían 40
# llamadas autenticadas en 10 minutos, que es justo el patrón que hace que
# Instagram mande la cuenta a checkpoint.
confirmar_viva() {
  curl -s -m 30 "$HEALTH?forzar=1&shortcode=$SHORTCODE" | grep -q '"ok": *true'
}

if [ "$MODO" = "--check" ]; then
  if esta_viva; then
    echo "$(date '+%F %H:%M') sesión viva"
    exit 0
  fi
  echo "$(date '+%F %H:%M') sesión CAÍDA — renovala en /admin → Instagram"
  # Corriendo por cron nadie mira la salida: el aviso tiene que aparecer en la
  # pantalla. Es el respaldo del aviso por WhatsApp que manda el server
  # (modules/ig_monitor): este solo llega si la Mac está prendida.
  osascript -e 'display notification "Los posts salen sin imagen ni transcripción. Renovala en /admin → Instagram" with title "Growi: sesión de Instagram caída" sound name "Basso"' 2>/dev/null || true
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
if [ "$MODO" = "--perfiles" ]; then
  exec "$VENV/bin/python" scripts/ig_cookies_from_browser.py \
       --browser "$NAVEGADOR" --listar-perfiles
fi

COOKIE=""
if [ "$MODO" = "--manual" ]; then
  # Sin Acceso Total al Disco: las cookies las pegás vos desde el Inspector Web
  # de Safari (Desarrollo → Almacenamiento → Cookies → instagram.com).
  echo "→ pegá las cookies a mano"
  COOKIE="$(python3 scripts/set_ig_cookies.py --stdout --no-env)"
else
  echo "→ leyendo la sesión de $NAVEGADOR${PERFIL:+ (perfil: $PERFIL)}"
  COOKIE="$("$VENV/bin/python" scripts/ig_cookies_from_browser.py \
              --browser "$NAVEGADOR" ${PERFIL:+--profile "$PERFIL"} \
              --stdout --no-env $EXTRA || true)"
fi

if ! echo "$COOKIE" | grep -q '"sessionid"'; then
  echo
  echo "no pude sacar la sesión de $NAVEGADOR, así que NO subo nada."
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
  if esta_viva && confirmar_viva; then echo "listo: la sesión de prod está viva"; exit 0; fi
  sleep 15
done

echo "la variable se subió pero el health sigue fallando después de 10 minutos."
echo "Mirá: railway logs -s $SERVICIO | grep ig_api"
exit 1
