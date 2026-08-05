"""
Monitor del camino hacia el CRM de Growi.

Existe por un caso concreto: el proxy de salida se cayó (cuenta de AWS
suspendida) y nos enteramos horas después, por el screenshot de un vendedor al
que le falló una campaña con los comentarios ya generados. El sistema sabía que
no había ruta al CRM mucho antes que nosotros; simplemente no se lo contaba a
nadie.

Este módulo corre un chequeo periódico en segundo plano y avisa por WhatsApp
cuando el estado CAMBIA (anda -> caído, caído -> anda). No manda un mensaje por
chequeo: eso se vuelve ruido y se ignora, que es lo mismo que no avisar.
"""
import os
import threading
import time

import requests

# Cada cuánto se chequea. 5 minutos es suficiente: el modo de falla real (una
# instancia que se apaga) dura horas o días, no segundos.
INTERVALO = float(os.environ.get("GROWI_HEALTH_INTERVAL", "300"))

# Si sigue caído, se recuerda cada tanto para que no se pierda entre mensajes
# viejos. Sin esto, una caída de fin de semana avisa una sola vez el viernes.
RECORDATORIO = float(os.environ.get("GROWI_HEALTH_REMINDER", "21600"))  # 6h

# A dónde avisar. Sin número configurado el monitor igual corre y loguea: sirve
# para el endpoint /health/growi aunque no haya canal de alertas.
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

_estado = {
    "ok": None,          # None = todavía no se chequeó
    "detalle": "sin chequear",
    "ultimo_chequeo": None,
    "caido_desde": None,
    "proxies": [],
}
_lock = threading.Lock()
_ultimo_aviso = 0.0
_arrancado = False


def estado() -> dict:
    """Snapshot para el endpoint /health/growi."""
    with _lock:
        e = dict(_estado)
    if e["caido_desde"]:
        e["caido_hace_min"] = round((time.time() - e["caido_desde"]) / 60)
    return e


def _avisar(texto: str) -> None:
    print(f"[growi-health] ALERTA: {texto}", flush=True)
    if not (ALERTA_TO and WHATSAPP_API_URL and WHATSAPP_TOKEN):
        return
    try:
        requests.post(
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
    except Exception as e:
        # El aviso no puede tumbar el monitor: si WhatsApp falla, queda el log.
        print(f"[growi-health] no pude mandar el aviso: {e!r}", flush=True)


def _chequear_una_vez() -> None:
    global _ultimo_aviso
    from modules.growi_client import (verificar_disponible, GrowiUnavailable,
                                      _POOL, _preflight_reset)

    # Sin el reset, el preflight devolvería el resultado cacheado del último
    # request de un vendedor y el monitor no estaría midiendo nada.
    _preflight_reset()
    try:
        verificar_disponible()
        ok, detalle = True, "hay ruta al CRM"
    except GrowiUnavailable as e:
        ok, detalle = False, str(e)
    except Exception as e:
        # Un error raro no es "está caído": no queremos despertar a nadie por
        # un bug nuestro. Se loguea y se deja el estado como estaba.
        print(f"[growi-health] chequeo no concluyente: {e!r}", flush=True)
        return

    ahora = time.time()
    with _lock:
        antes = _estado["ok"]
        _estado.update({"ok": ok, "detalle": detalle, "ultimo_chequeo": ahora,
                        "proxies": _POOL.estado()})
        if ok:
            _estado["caido_desde"] = None
        elif _estado["caido_desde"] is None:
            _estado["caido_desde"] = ahora
        caido_desde = _estado["caido_desde"]

    if ok and antes is False:
        _avisar("✅ Growi volvió: el CRM está accesible de nuevo. "
                "Ya se pueden mandar órdenes.")
        _ultimo_aviso = 0.0
    elif not ok and antes is not False:
        # Primera vez que lo vemos caído (incluye el arranque con todo caído).
        # El CRM NO tiene whitelist de IPs (verificado): acepta el login desde
        # cualquier lado. Si no hay ruta, el problema es el proxy en sí, o la
        # conexión. Decirlo bien importa: mandar a "revisar la whitelist" hace
        # perder tiempo buscando un permiso que no existe.
        _avisar(f"🔴 No hay conexión con el CRM de Growi. Los vendedores no pueden "
                f"publicar órdenes.\n\nDetalle: {detalle}\n"
                f"Revisar que el proxy de salida esté vivo. Si no vuelve, se puede "
                f"trabajar sin proxy: borrar GROWI_HTTP_PROXY y vaciar el proxy de "
                f"cada vendedor en /admin.")
        _ultimo_aviso = ahora
    elif not ok and ahora - _ultimo_aviso > RECORDATORIO:
        minutos = round((ahora - caido_desde) / 60)
        _avisar(f"🔴 Growi sigue sin conexión (hace {minutos} min). "
                f"Los vendedores siguen sin poder publicar.")
        _ultimo_aviso = ahora


def _loop() -> None:
    while True:
        try:
            _chequear_una_vez()
        except Exception as e:
            print(f"[growi-health] error en el loop: {e!r}", flush=True)
        time.sleep(INTERVALO)


def arrancar() -> None:
    """Arranca el monitor en un thread daemon. Idempotente: llamarlo dos veces
    no levanta dos monitores (con gunicorn y varios workers, cada proceso tiene
    el suyo, que es lo esperado)."""
    global _arrancado
    with _lock:
        if _arrancado:
            return
        _arrancado = True
    threading.Thread(target=_loop, daemon=True, name="growi-health").start()
    print(f"[growi-health] monitor activo (cada {INTERVALO:.0f}s, "
          f"avisos a {'WhatsApp ' + ALERTA_TO if ALERTA_TO else 'solo log'})", flush=True)
