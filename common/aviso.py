"""
Avisos operativos: el canal por el que el sistema pide ayuda.

Existía dentro de growi_monitor y se sacó acá cuando apareció el segundo
monitor (el de la sesión de Instagram). Es la misma lógica y el mismo destino:
duplicarla significaba que arreglar el envío en un lado dejaba el otro mudo.

Detalle que costó caro: si faltan las variables de WhatsApp, la versión vieja
hacía `return` en silencio. En producción `WHATSAPP_API_URL` y
`WHATSAPP_ACCESS_TOKEN` están en el servicio `whatsapp` y NO en `openai`, así
que el monitor del CRM creía que avisaba y no avisaba a nadie. Ahora eso se
grita en el log la primera vez.

Y el segundo, peor: WhatsApp NO SIRVE SOLO para esto. La API de Meta contesta
200 con un id de mensaje y después lo descarta si pasaron más de 24 h desde que
el destinatario le escribió al bot (error 131047, "Re-engagement message", que
solo aparece en el webhook). O sea que el envío parecía exitoso desde acá y no
llegaba nada. Revisando el log del servicio `whatsapp` había entregas fallidas
de todo el día: ninguna alerta había llegado nunca. Y es justo el peor modo de
falla posible, porque las caídas pasan los fines de semana, que es cuando más
tiempo hace que nadie le escribió al bot.

Por eso ahora hay DOS canales y se manda a los dos. Telegram no tiene ventana
ni plantillas que aprobar: un bot le escribe a su chat cuando quiere.
"""
import os

import requests

# A dónde avisar. Sin número configurado los monitores igual corren y loguean.
_ALERTA_TO_RAW = os.environ.get("GROWI_ALERTA_WHATSAPP", "")

# Meta ENTREGA los mensajes con un número y solo ACEPTA enviarlos a otro: el
# 9 de celular y el código de área difieren del que quedó registrado. Mismo
# mapeo que whatsappService.normalizar_numero; se repite acá porque son
# servicios distintos y una alerta que falla en silencio es peor que no tenerla.
_NUMEROS = {"5492233407778": "54223153407778"}


def _normalizar_numero(n: str) -> str:
    n = (n or "").strip().lstrip("+").replace(" ", "").replace("-", "")
    return _NUMEROS.get(n, n)


ALERTA_TO = _normalizar_numero(_ALERTA_TO_RAW)
WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")

# Telegram: el canal confiable. El token sale de @BotFather (es el del bot que
# ya usan los vendedores) y el chat es al que se avisa; se saca una sola vez con
# scripts/telegram_chat_id.py después de mandarle /start al bot.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT = os.environ.get("TELEGRAM_ALERTA_CHAT_ID", "").strip()

_sin_canal_avisado = False


def hay_whatsapp() -> bool:
    return bool(ALERTA_TO and WHATSAPP_API_URL and WHATSAPP_TOKEN)


def hay_telegram() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT)


def hay_canal() -> bool:
    return hay_whatsapp() or hay_telegram()


def _por_telegram(texto: str, etiqueta: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": texto,
                  "disable_web_page_preview": True},
            timeout=15,
        )
        if r.status_code >= 300:
            print(f"[{etiqueta}] Telegram rechazó el aviso: "
                  f"{r.status_code} {r.text[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[{etiqueta}] no pude mandar el aviso por Telegram: {e!r}", flush=True)
        return False


def _por_whatsapp(texto: str, etiqueta: str) -> bool:
    """OJO con lo que significa el True de acá: que Meta ACEPTÓ el mensaje, no
    que haya llegado. Si la ventana de 24 h está cerrada lo descarta después, y
    eso solo se ve en el webhook (servicio `whatsapp`, error 131047)."""
    try:
        r = requests.post(
            WHATSAPP_API_URL,
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": ALERTA_TO,
                "type": "text",
                "text": {"body": texto},
            },
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        if r.status_code >= 300:
            print(f"[{etiqueta}] WhatsApp rechazó el aviso: "
                  f"{r.status_code} {r.text[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[{etiqueta}] no pude mandar el aviso por WhatsApp: {e!r}", flush=True)
        return False


def enviar(texto: str, etiqueta: str = "aviso") -> bool:
    """Manda el aviso por todos los canales configurados.

    Devuelve si salió por AL MENOS UNO. Se mandan los dos a propósito y no uno
    de respaldo del otro: no hay forma de saber en el momento si el de WhatsApp
    se va a entregar, así que esperar su fracaso para recién ahí probar Telegram
    sería esperar una señal que nunca llega.

    Nunca levanta: el que avisa de una caída no puede caerse por avisar.
    """
    global _sin_canal_avisado
    print(f"[{etiqueta}] ALERTA: {texto}", flush=True)

    if not hay_canal():
        if not _sin_canal_avisado:
            _sin_canal_avisado = True
            print(f"[{etiqueta}] las alertas NO se están mandando a nadie: falta "
                  "TELEGRAM_BOT_TOKEN + TELEGRAM_ALERTA_CHAT_ID (o las de WhatsApp) "
                  "en este servicio. Queda solo el log.", flush=True)
        return False

    salio = False
    if hay_telegram():
        salio = _por_telegram(texto, etiqueta) or salio
    if hay_whatsapp():
        salio = _por_whatsapp(texto, etiqueta) or salio
    return salio
