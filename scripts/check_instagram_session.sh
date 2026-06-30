#!/usr/bin/env bash
# Chequea que la sesión de instaloader siga viva contra el endpoint de salud
# del openai-service. Pensado para correr por cron en la misma máquina/servidor
# donde vive el contenedor (pega a localhost, no a internet).
#
# Configuración (vía variables de entorno o editando este archivo):
#   HEALTH_URL            URL del endpoint de salud (default: http://localhost:8000/health/instagram)
#   TEST_SHORTCODE        shortcode de un post público y estable para probar
#   SLACK_WEBHOOK_URL     opcional, si está seteado manda la alerta a Slack
#   ALERT_EMAIL           opcional, si está seteado manda la alerta por mail (requiere `mail` instalado)
#
# Crontab sugerido (chequea cada 6 horas):
#   0 */6 * * * /Users/abc/Desktop/CROW/chatbot-ai/scripts/check_instagram_session.sh >> /tmp/ig_session_check.log 2>&1

set -euo pipefail

HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health/instagram}"
TEST_SHORTCODE="${TEST_SHORTCODE:-}"

if [ -z "$TEST_SHORTCODE" ]; then
  echo "$(date -Iseconds) [check_instagram_session] ERROR: falta TEST_SHORTCODE (un post público estable, ej. de una cuenta oficial grande)"
  exit 1
fi

RESPONSE=$(curl -s -m 20 "${HEALTH_URL}?shortcode=${TEST_SHORTCODE}" || echo '{"ok": false, "error": "curl_failed_or_timeout"}')
OK=$(echo "$RESPONSE" | grep -o '"ok": *true' || true)

if [ -n "$OK" ]; then
  echo "$(date -Iseconds) [check_instagram_session] OK: $RESPONSE"
  exit 0
fi

MSG="🚨 Sesión de Instagram (crowagency.ofc) caída. Respuesta: ${RESPONSE}. Renovar con: instaloader --login crowagency.ofc"
echo "$(date -Iseconds) [check_instagram_session] FALLO: $MSG"

if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
  curl -s -X POST -H 'Content-type: application/json' \
    --data "{\"text\": \"${MSG}\"}" \
    "$SLACK_WEBHOOK_URL" >/dev/null || true
fi

if [ -n "${ALERT_EMAIL:-}" ] && command -v mail >/dev/null 2>&1; then
  echo "$MSG" | mail -s "Sesión de Instagram caída - chatbot-ai" "$ALERT_EMAIL" || true
fi

exit 1
