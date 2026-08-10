"""
Worker de mantenimiento de tablas (caché de posts y auditoría del CRM).

OJO: el reintento de las órdenes pendientes YA NO vive acá — se mudó a
webService/orden_cola.py. El motivo es que el reenvío tiene que salir con las
credenciales de la cuenta que cargó la orden, y las sesiones por cuenta contra
el CRM están en el webService. Desde este servicio se reenviaba con las
credenciales globales del .env, así que la orden entraba en el CRM de otra
cuenta y el vendedor nunca la veía en su gestor.

Lo que quedó acá es el mantenimiento periódico, que no toca el CRM y ya tenía
su hilo corriendo.
"""
import os
import threading
import time

# Cada cuánto corre el mantenimiento.
INTERVALO = float(os.environ.get("GROWI_QUEUE_INTERVAL", "60"))

# Mantenimiento del caché de posts (tabla post_cache), de arrimo en este worker.
# Cada 6 horas se borran los posts de más de 30 días: nadie vuelve a pegar un
# link de hace un mes, y cada fila carga la imagen en base64 (~5 KB).
_PURGA_CADA = float(os.environ.get("POST_CACHE_PURGA_CADA", str(6 * 3600)))
_PURGA_DIAS = int(os.environ.get("POST_CACHE_PURGA_DIAS", "30"))

# Retención de la auditoría de llamadas al CRM (tabla growi_calls). 30 días
# cubre de sobra el caso real: un vendedor que reclama por una orden de la
# semana pasada.
_TRAZAS_DIAS = int(os.environ.get("GROWI_TRAZAS_DIAS", "30"))

_arrancado = False
_lock = threading.Lock()


def _repo():
    from common import repository
    return repository


def _loop() -> None:
    ultima_purga = 0.0
    while True:
        # Mantenimiento del caché de posts. Va acá porque este worker ya corre
        # solo y ya tiene DB: no hace falta otro hilo para un DELETE por día.
        # Nunca en el camino de una generación, para no sumarle latencia.
        if time.time() - ultima_purga > _PURGA_CADA:
            ultima_purga = time.time()
            try:
                _repo().post_cache_purgar(dias=_PURGA_DIAS)
            except Exception as e:
                print(f"[mantenimiento] error purgando el caché de posts: {e!r}", flush=True)
            # Misma idea para la auditoría del CRM: es un log, no un registro
            # contable. Sin purga la tabla crece para siempre con cuerpos de
            # respuesta (una página de login son varios KB).
            try:
                _repo().growi_calls_purgar(dias=_TRAZAS_DIAS)
            except Exception as e:
                print(f"[mantenimiento] error purgando las trazas del CRM: {e!r}", flush=True)

        time.sleep(INTERVALO)


def arrancar() -> None:
    """Arranca el mantenimiento si hay DB. Sin DB no hay nada que purgar."""
    global _arrancado
    try:
        from common.db import db_available
        if not db_available():
            print("[mantenimiento] sin DATABASE_URL: no hay nada que purgar", flush=True)
            return
    except Exception as e:
        print(f"[mantenimiento] no pude verificar la DB: {e!r}", flush=True)
        return

    with _lock:
        if _arrancado:
            return
        _arrancado = True
    threading.Thread(target=_loop, daemon=True, name="growi-mantenimiento").start()
    print(f"[mantenimiento] worker de purgas activo (cada {INTERVALO:.0f}s)", flush=True)
