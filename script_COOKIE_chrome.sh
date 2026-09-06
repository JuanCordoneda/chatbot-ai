#!/usr/bin/env bash
# Renueva la sesión de Instagram de producción leyendo CHROME, perfil "Juan Cruz".
#
# El perfil va FIJO y es el punto de todo esto: browser-cookie3 arma una lista
# con Default y "Profile *" y se queda con la primera que existe. En esta Mac esa
# primera es Default, que tiene la sesión vieja de @crowagency.ofc — la filtrada.
# O sea que sin fijar el perfil, este script leía la cuenta equivocada aunque
# acabaras de loguearte con la nueva en otra ventana.
#
# Se puede pisar sin editar el archivo:
#   IG_PROFILE="Crow Agency" ./script_COOKIE_chrome.sh
#   ./script_COOKIE_chrome.sh --profile Default
#   ./script_COOKIE_chrome.sh --perfiles     ← qué perfiles hay y cómo se llaman
#
# En macOS leer las cookies de Chrome pide acceso al Llavero: sale un diálogo
# del sistema y hay que aceptarlo.
#
# Toda la lógica está en script_COOKIE.sh: esto solo elige navegador y perfil.
exec "$(dirname "$0")/script_COOKIE.sh" --chrome \
     --profile "${IG_PROFILE:-Juan Cruz}" "$@"
