#!/usr/bin/env bash
# Renueva la sesión de Instagram de producción leyendo SAFARI.
#
# Existe para no depender de acordarse de un flag: con más de una cuenta, cada
# una vive en un navegador distinto y el error fácil es correr el script y subir
# sin querer la cuenta equivocada. Acá el nombre del archivo dice cuál sube.
#
# Safari no tiene perfiles: lo que haya logueado ahí es lo que sube.
#
# Toda la lógica está en script_COOKIE.sh: esto solo elige el navegador. Los
# argumentos se pasan tal cual, así que --check y --manual siguen andando.
exec "$(dirname "$0")/script_COOKIE.sh" --safari "$@"
