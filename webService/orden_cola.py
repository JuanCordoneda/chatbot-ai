"""
Worker de la cola de órdenes pendientes.

Cuando el envío al CRM falla por red, la orden queda guardada en la tabla
`pending_orders` y este worker la reintenta con backoff hasta que entra. El
objetivo es que una caída del proxy sea un RETRASO y no trabajo perdido: antes,
el vendedor tenía que regenerar los comentarios y recargar todo a mano.

Vive en el webService (antes estaba en openAIService/modules/orden_queue.py)
porque el reintento tiene que salir con las credenciales de la CUENTA que cargó
la orden, y las sesiones por cuenta contra el CRM están acá. Reintentando desde
el otro servicio, la orden se reenviaba con las credenciales globales del .env
y entraba en el CRM equivocado — el mismo bug que hacía que no apareciera en el
gestor del vendedor.

Reglas que no se negocian:
  - Solo se reintenta lo que sabemos que nunca salió (fallo al conectar).
    enviar_trafico.php no es idempotente; reintentar a ciegas duplica órdenes.
  - El claim de cada orden es atómico en la DB, así dos workers no mandan la
    misma dos veces.
"""
import os
import threading
import time

# Cada cuánto mira la cola. Corto: lo caro es el envío, no el SELECT.
INTERVALO = float(os.environ.get("GROWI_QUEUE_INTERVAL", "60"))

_arrancado = False
_lock = threading.Lock()


def _repo():
    from common import repository
    return repository


def procesar_una(enviar) -> bool:
    """Toma una orden vencida y la intenta. True si procesó algo.

    Devolver si hubo trabajo permite vaciar la cola de corrido cuando el CRM
    vuelve, en vez de mandar una orden por minuto.

    `enviar(orden)` es el envío real, inyectado por el webService: devuelve el
    dict de respuesta del CRM o levanta excepción.
    """
    repo = _repo()
    orden = repo.tomar_orden_para_reintentar()
    if not orden:
        return False

    oid = orden["id"]
    intentos = orden.get("intentos", 0)
    print(f"[cola] reintentando orden {oid} (intento {intentos}) "
          f"de {orden.get('post_url')}", flush=True)

    try:
        crm = enviar(orden)
    except Exception as e:
        # Distinguir "no salió" de "pudo haber entrado" es plata: lo segundo no
        # se reintenta nunca solo. Lo decide el webService, que es quien conoce
        # el tipo de error; acá solo se respeta la marca.
        reintentable = getattr(e, "reintentable", None)
        if reintentable is None:
            # Error que no es de red (credenciales, payload inválido). Reintentar
            # no lo va a arreglar solo, pero tampoco lo tiramos: queda a revisión.
            reintentable = False
        repo.reprogramar_orden(oid, f"{e.__class__.__name__}: {e}",
                               reintentable=reintentable)
        estado = "reprogramada" if reintentable else "a revisar"
        print(f"[cola] orden {oid} {estado}: {e!r}", flush=True)
        return True

    if crm and crm.get("success"):
        repo.marcar_orden_enviada(oid)
        print(f"[cola] orden {oid} enviada OK ({crm.get('insertadas', 0)} insertadas)", flush=True)
    else:
        # El CRM contestó pero rechazó. No es un problema de red: no tiene
        # sentido reintentarlo en loop, lo mira un humano.
        errores = "; ".join((crm or {}).get("errors") or []) or "el CRM rechazó la orden"
        repo.reprogramar_orden(oid, errores, reintentable=False)
        print(f"[cola] orden {oid} rechazada por el CRM: {errores}", flush=True)
    return True


def _loop(enviar) -> None:
    while True:
        # Antes de tomar trabajo nuevo, rescatar lo que quedó colgado en
        # 'enviando' por un reinicio del servicio. Si no, esas filas no las
        # vuelve a mirar nadie: el claim solo busca 'pendiente'.
        try:
            _repo().revisar_ordenes_colgadas()
        except Exception as e:
            print(f"[cola] error revisando órdenes colgadas: {e!r}", flush=True)

        try:
            # Vacía todo lo que esté vencido, con tope por vuelta para no
            # monopolizar el proceso si la cola quedó larga tras una caída.
            for _ in range(20):
                if not procesar_una(enviar):
                    break
        except Exception as e:
            print(f"[cola] error en el loop: {e!r}", flush=True)
        time.sleep(INTERVALO)


def arrancar(enviar) -> None:
    """Arranca el worker si hay DB. Sin DB no hay cola: el sistema se comporta
    como antes (el error se le informa al vendedor en el momento)."""
    global _arrancado
    try:
        from common.db import db_available
        if not db_available():
            print("[cola] sin DATABASE_URL: no hay cola de reintentos", flush=True)
            return
    except Exception as e:
        print(f"[cola] no pude verificar la DB: {e!r}", flush=True)
        return

    with _lock:
        if _arrancado:
            return
        _arrancado = True
    threading.Thread(target=_loop, args=(enviar,), daemon=True,
                     name="growi-cola").start()
    print(f"[cola] worker de reintentos activo (cada {INTERVALO:.0f}s)", flush=True)
