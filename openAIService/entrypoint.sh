#!/bin/sh
# Arranque del openAIService.
#
# Aplica las migraciones antes de levantar la app para que un deploy nuevo (Railway)
# no requiera ningún paso manual. `alembic upgrade head` es idempotente: si la DB ya
# está al día, no hace nada.
#
# Si DATABASE_URL no está seteada, se saltea todo y la app arranca igual (cae al
# fallback de archivos, que es el comportamiento previo a la etapa 2).
set -e

if [ -n "$DATABASE_URL" ]; then
  echo "[entrypoint] aplicando migraciones..."
  # La DB puede tardar unos segundos en aceptar conexiones cuando arranca todo junto.
  i=1
  while [ "$i" -le 10 ]; do
    if alembic upgrade head; then
      echo "[entrypoint] migraciones OK"
      break
    fi
    if [ "$i" -eq 10 ]; then
      # No abortamos: sin migraciones la app cae al fallback de archivos, que es
      # preferible a un contenedor en crash-loop.
      echo "[entrypoint] ADVERTENCIA: no pude aplicar las migraciones tras 10 intentos; sigo sin DB" >&2
      break
    fi
    echo "[entrypoint] la DB todavía no responde (intento $i/10), reintento en 3s..."
    sleep 3
    i=$((i + 1))
  done

  # Seed inicial opcional (cuenta + clientes + usuarios). Idempotente, pero se
  # corre solo si se pide explícitamente con RUN_SEED=1.
  if [ "$RUN_SEED" = "1" ]; then
    echo "[entrypoint] corriendo seed inicial..."
    python scripts/seed_facu.py || echo "[entrypoint] ADVERTENCIA: el seed falló" >&2
  fi
else
  echo "[entrypoint] DATABASE_URL no seteada: arranco sin DB (fallback de archivos)"
fi

exec "$@"
