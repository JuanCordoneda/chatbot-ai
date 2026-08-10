#!/usr/bin/env bash
# Corre las pruebas del camino de envío de órdenes. Antes de cada deploy.
#
#   ./scripts/tests/correr.sh
#
# Corren DENTRO del contenedor web-service (ahí están flask, requests y la DB).
# Ninguna toca el CRM: todas rompen `requests` antes de importar la app, así que
# si algún camino se escapa del CRM falso, el test falla en vez de cargar una
# orden de verdad.
set -uo pipefail

CONTENEDOR="${CONTENEDOR:-chatbot-ai-web-service-1}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTENEDOR"; then
  echo "El contenedor $CONTENEDOR no está levantado."
  echo "Probá:  docker compose -p chatbot-ai up -d"
  exit 2
fi

# Los tests se copian en cada corrida: así se prueba SIEMPRE el archivo del
# repo, no una copia vieja que haya quedado adentro.
for f in "$(dirname "$0")"/test_*.py; do
  docker cp "$f" "$CONTENEDOR:/tmp/$(basename "$f")" >/dev/null
done

total=0
fallaron=0
for f in "$(dirname "$0")"/test_*.py; do
  t="$(basename "$f")"
  printf '%-24s ' "$t"
  salida=$(docker exec -e PYTHONPATH=/app "$CONTENEDOR" python -B "/tmp/$t" 2>&1)
  codigo=$?
  n=$(echo "$salida" | grep -cE '^  OK')
  total=$((total + n))
  if [ $codigo -eq 0 ]; then
    echo "$n checks OK"
  else
    fallaron=$((fallaron + 1))
    echo "FALLÓ"
    echo "$salida" | grep -E '^(  FALLA|         →)' | sed 's/^/    /'
  fi
done

echo "──────────────────────────────────────────"
if [ $fallaron -eq 0 ]; then
  echo "TODO OK — $total checks"
else
  echo "$fallaron suite(s) con fallas — $total checks pasaron"
fi
exit $((fallaron > 0))
