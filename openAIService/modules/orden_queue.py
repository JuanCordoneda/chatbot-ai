"""
Worker de la cola de órdenes pendientes.

Cuando el envío al CRM falla por red, la orden queda guardada en la tabla
`pending_orders` y este worker la reintenta con backoff hasta que entra. El
objetivo es que una caída del proxy sea un RETRASO y no trabajo perdido: antes,
el vendedor tenía que regenerar los comentarios y recargar todo a mano.

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

# Mantenimiento del caché de posts (tabla post_cache), de arrimo en este worker.
# Cada 6 horas se borran los posts de más de 30 días: nadie vuelve a pegar un
# link de hace un mes, y cada fila carga la imagen en base64 (~5 KB).
_PURGA_CADA = float(os.environ.get("POST_CACHE_PURGA_CADA", str(6 * 3600)))
_PURGA_DIAS = int(os.environ.get("POST_CACHE_PURGA_DIAS", "30"))

_arrancado = False
_lock = threading.Lock()


def _repo():
    from common import repository
    return repository


def procesar_una() -> bool:
    """Toma una orden vencida y la intenta. True si procesó algo.

    Devolver si hubo trabajo permite vaciar la cola de corrido cuando el CRM
    vuelve, en vez de mandar una orden por minuto.
    """
    from modules.growi_client import ejecutar_campana, GrowiUnavailable

    repo = _repo()
    orden = repo.tomar_orden_para_reintentar()
    if not orden:
        return False

    oid = orden["id"]
    payload = orden.get("payload") or {}
    intentos = orden.get("intentos", 0)
    print(f"[cola] reintentando orden {oid} (intento {intentos}) "
          f"de {orden.get('post_url')}", flush=True)

    try:
        resultado = ejecutar_campana(
            orden["post_url"],
            payload.get("comentarios") or [],
            payload.get("ordenes") or [],
            float(payload.get("disponible") or 0),
        )
    except GrowiUnavailable as e:
        repo.reprogramar_orden(oid, str(e), reintentable=e.reintentable)
        estado = "a revisar" if not e.reintentable else "reprogramada"
        print(f"[cola] orden {oid} {estado}: {e}", flush=True)
        return True
    except Exception as e:
        # Error que no es de red (credenciales, payload inválido). Reintentar no
        # lo va a arreglar solo, pero tampoco lo tiramos: queda para revisión.
        repo.reprogramar_orden(oid, f"{e.__class__.__name__}: {e}", reintentable=False)
        print(f"[cola] orden {oid} a revisión por error no recuperable: {e!r}", flush=True)
        return True

    if resultado and resultado.success:
        repo.marcar_orden_enviada(oid)
        print(f"[cola] orden {oid} enviada OK ({resultado.insertadas} insertadas)", flush=True)
    else:
        # El CRM contestó pero rechazó. No es un problema de red: no tiene
        # sentido reintentarlo en loop, lo mira un humano.
        errores = "; ".join(resultado.errors) if resultado and resultado.errors else "el CRM rechazó la orden"
        repo.reprogramar_orden(oid, errores, reintentable=False)
        print(f"[cola] orden {oid} rechazada por el CRM: {errores}", flush=True)
    return True


def _loop() -> None:
    ultima_purga = 0.0
    while True:
        try:
            # Vacía todo lo que esté vencido, con tope por vuelta para no
            # monopolizar el proceso si la cola quedó larga tras una caída.
            for _ in range(20):
                if not procesar_una():
                    break
        except Exception as e:
            print(f"[cola] error en el loop: {e!r}", flush=True)

        # Mantenimiento del caché de posts. Va acá porque este worker ya corre
        # solo y ya tiene DB: no hace falta otro hilo para un DELETE por día.
        # Nunca en el camino de una generación, para no sumarle latencia.
        if time.time() - ultima_purga > _PURGA_CADA:
            ultima_purga = time.time()
            try:
                _repo().post_cache_purgar(dias=_PURGA_DIAS)
            except Exception as e:
                print(f"[cola] error purgando el caché de posts: {e!r}", flush=True)

        time.sleep(INTERVALO)


def arrancar() -> None:
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
    threading.Thread(target=_loop, daemon=True, name="growi-cola").start()
    print(f"[cola] worker de reintentos activo (cada {INTERVALO:.0f}s)", flush=True)
