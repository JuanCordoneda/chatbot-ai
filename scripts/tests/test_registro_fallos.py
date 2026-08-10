"""
¿Queda registro de TODO envío que falla, incluso del que nunca sale a la red?

El agujero: `trazar` solo envuelve el round-trip HTTP. Un envío frenado antes
(sin campaña, sin idvendedor, sin CRM propio) no dejaba ninguna fila, y el panel
de trazas quedaba vacío justo en el caso que el vendedor viene a reclamar.
"""
import sys

sys.path.insert(0, "/app")

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido

import app as web                                                   # noqa: E402
from common import repository                                       # noqa: E402

FILAS = []
repository.registrar_llamada_crm = lambda **f: FILAS.append(f) or True

FALLOS = []


def chequear(caso, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {caso}" + (f" — {detalle}" if detalle else ""))
    if not cond:
        FALLOS.append(caso)


ORDENES = [{"prod": "Likes 1700 JAP", "url": "https://instagram.com/p/X/",
            "cantidad": "400", "costo": 0.055, "comentarios": []}]


def envio_que_falla(nombre, parchear):
    """Corre un envío que se frena antes de salir y devuelve las filas nuevas."""
    FILAS.clear()
    deshacer = parchear()
    try:
        web._enviar_ordenes_crm(
            list(ORDENES), post_url="https://instagram.com/p/X/",
            cliente_ig="cliente.test", account_id=99, user_id=7,
            username="pedro", origen="web")
        chequear(nombre, False, "no lanzó ningún error, se esperaba que frenara")
    except Exception as e:
        print(f"\n[{nombre}] frenó con: {e.__class__.__name__}")
    finally:
        deshacer()
    return list(FILAS)


# 1) La cuenta no tiene CRM propio.
def _sin_crm():
    orig = web._cuenta_tiene_crm_propio
    web._cuenta_tiene_crm_propio = lambda acc: False
    return lambda: setattr(web, "_cuenta_tiene_crm_propio", orig)


filas = envio_que_falla("cuenta sin CRM propio", _sin_crm)
chequear("cuenta sin CRM propio deja fila", len(filas) == 1, f"{len(filas)} fila/s")
if filas:
    f = filas[0]
    chequear("  la fila está marcada como fallida", f["ok"] is False)
    chequear("  dice el motivo", "CuentaSinCRM" in (f["error"] or ""), f["error"])
    chequear("  guarda de qué post", f["post_url"] == "https://instagram.com/p/X/")
    chequear("  guarda el cliente", f["client_ig_username"] == "cliente.test")
    chequear("  guarda quién la mandó", f["username"] == "pedro" and f["account_id"] == 99)


# 2) No se pudo determinar la campaña (el caso de Pedro).
def _sin_campania():
    o1, o2 = web._cuenta_tiene_crm_propio, web.resolver_venta
    o3 = web._idvendedor_del_crm
    web._cuenta_tiene_crm_propio = lambda acc: True
    web.resolver_venta = lambda *a, **kw: {
        "idventa": None, "idvendedor": None, "saldo": None,
        "origen": "test", "detalle": "sin campaña"}
    web._idvendedor_del_crm = lambda acc: None

    def deshacer():
        web._cuenta_tiene_crm_propio, web.resolver_venta = o1, o2
        web._idvendedor_del_crm = o3
    return deshacer


filas = envio_que_falla("sin campaña resuelta", _sin_campania)
chequear("sin campaña deja fila", len(filas) == 1, f"{len(filas)} fila/s")
if filas:
    f = filas[0]
    chequear("  la fila está marcada como fallida", f["ok"] is False)
    chequear("  dice que no se pudo determinar", "determinar" in (f["error"] or ""), f["error"])
    chequear("  guarda las órdenes que se rebotaron",
             (f["request_payload"] or {}).get("ordenes") is not None
             or "ordenes" in str(f["request_payload"]),
             str(f["request_payload"])[:120])
    chequear("  aparece en el filtro de errores del panel", f["ok"] is False)


# 3) El request SÍ sale y falla: no se duplica la fila.
def _falla_la_red():
    o1, o2 = web._cuenta_tiene_crm_propio, web.resolver_venta
    o3, o4 = web._growi_request, web._corregir_costo
    web._cuenta_tiene_crm_propio = lambda acc: True
    web.resolver_venta = lambda *a, **kw: {
        "idventa": "33113", "idvendedor": "77", "saldo": 26.4,
        "origen": "test", "detalle": "campaña de prueba"}
    web._corregir_costo = lambda o, acc: None

    def revienta(method, path, *a, **kw):
        # Simula _growi_request: traza SOLO el envío de órdenes (las consultas
        # de apoyo, como la hora del server, no se trazan) y re-lanza.
        from common.growi_trace import trazar
        error = _rq.exceptions.ConnectionError("proxy caído")
        if path.split("?")[0] != web._PATH_ENVIO_ORDENES:
            raise error
        with trazar("enviar_trafico", "POST", "http://crm/enviar_trafico.php"):
            raise error

    web._growi_request = revienta

    def deshacer():
        web._cuenta_tiene_crm_propio, web.resolver_venta = o1, o2
        web._growi_request, web._corregir_costo = o3, o4
    return deshacer


filas = envio_que_falla("la red se cae con el request ya afuera", _falla_la_red)
chequear("el request que salió deja UNA sola fila (no se duplica)",
         len(filas) == 1, f"{len(filas)} fila/s: {[x['operacion'] for x in filas]}")
if filas:
    chequear("  y está marcada como fallida", filas[0]["ok"] is False)


print()
if FALLOS:
    print(f"FALLARON {len(FALLOS)}: " + " | ".join(FALLOS))
    sys.exit(1)
print("TODO OK — todo envío fallido deja registro, y ninguno se duplica")
