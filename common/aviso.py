"""
Avisos operativos por WhatsApp: el canal por el que el sistema pide ayuda.

Existía dentro de growi_monitor y se sacó acá cuando apareció el segundo
monitor (el de la sesión de Instagram). Es la misma lógica y el mismo destino:
duplicarla significaba que arreglar el envío en un lado dejaba el otro mudo.

Detalle que costó caro: si faltan las variables de WhatsApp, la versión vieja
hacía `return` en silencio. En producción `WHATSAPP_API_URL` y
`WHATSAPP_ACCESS_TOKEN` están en el servicio `whatsapp` y NO en `openai`, así
que el monitor del CRM creía que avisaba y no avisaba a nadie. Ahora eso se
grita en el log la primera vez.
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

_sin_canal_avisado = False


def hay_canal() -> bool:
    return bool(ALERTA_TO and WHATSAPP_API_URL and WHATSAPP_TOKEN)


def enviar(texto: str, etiqueta: str = "aviso") -> bool:
    """Manda el aviso. Devuelve si salió. Nunca levanta: el que avisa de una
    caída no puede caerse por avisar."""
    global _sin_canal_avisado
    print(f"[{etiqueta}] ALERTA: {texto}", flush=True)

    if not hay_canal():
        if not _sin_canal_avisado:
            _sin_canal_avisado = True
            faltan = [n for n, v in (("GROWI_ALERTA_WHATSAPP", ALERTA_TO),
                                     ("WHATSAPP_API_URL", WHATSAPP_API_URL),
                                     ("WHATSAPP_ACCESS_TOKEN", WHATSAPP_TOKEN)) if not v]
            print(f"[{etiqueta}] las alertas NO se están mandando a nadie: "
                  f"falta {', '.join(faltan)} en este servicio. Queda solo el log.",
                  flush=True)
        return False

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
            # Un 400 de Meta (plantilla, ventana de 24 h, número no registrado)
            # devolvía 200 para nosotros y el aviso se perdía igual.
            print(f"[{etiqueta}] WhatsApp rechazó el aviso: "
                  f"{r.status_code} {r.text[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[{etiqueta}] no pude mandar el aviso: {e!r}", flush=True)
        return False
