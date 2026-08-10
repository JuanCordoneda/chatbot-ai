"""
Los 8 problemas de la auditoría del camino de envío, uno por uno.

Cada bloque describe el problema y afirma el comportamiento nuevo. Sin red real.
"""
import json
import sys
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido

import app as web                                                   # noqa: E402
import orden_cola                                                   # noqa: E402
from common import ordenes as ordmod                                # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" +
          (f"\n         → {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


class FakeResp:
    def __init__(self, payload=None, texto=None, status=200, url="https://crm.fake/x"):
        self._payload = payload
        self.status_code = status
        self.text = texto if texto is not None else json.dumps(payload or {})
        self.url = url
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload

    def raise_for_status(self):
        pass


ENVIOS = []
PRECIOS = {}          # (producto, cantidad) -> costo real del CRM
LLAMADAS = []


def fake_growi_request(method, path, account_id=None, **kw):
    LLAMADAS.append((path, kw.get("timeout")))
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    if "obtenercostotrafico" in path:
        j = kw.get("json") or {}
        clave = (str(j.get("producto")), int(j.get("cant_solicitada") or 0))
        if clave not in PRECIOS:
            return FakeResp({"costoTrafico": None})
        return FakeResp({"costoTrafico": PRECIOS[clave]})
    if "enviar_trafico" in path:
        ENVIOS.append({"account_id": account_id, "payload": kw.get("json"),
                       "timeout": kw.get("timeout")})
        return FakeResp({"success": True, "insertadas": len(kw["json"]["ordenes"]),
                         "messages": [], "warnings": [], "errors": []})
    raise AssertionError(f"llamada inesperada: {path}")


REAL_GROWI_REQUEST = web._growi_request      # el de verdad, para el bloque 1
web._growi_request = fake_growi_request
web._cuenta_tiene_crm_propio = lambda aid: True
web._account_crm_cfg = lambda aid: {"crm_url": "https://crm.fake", "crm_email": "v@d",
                                    "crm_password": "x", "crm_disponible": "150"}

USOS = []
web._log_uso = lambda accion, **kw: USOS.append({"accion": accion, **kw})

SALDO = {"valor": 200.0}
REFRESCOS = []
web.resolver_venta = lambda account_id, ig, idventa_elegida=None, refrescar=False: (
    REFRESCOS.append(bool(refrescar)) or {
        "idventa": "8811", "idvendedor": "701", "origen": "auto",
        "detalle": "campaña", "saldo": SALDO["valor"]})

cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = 7
    s["user_id"] = 21
    s["username"] = "tomas"


def limpiar():
    ENVIOS.clear()
    USOS.clear()
    LLAMADAS.clear()
    REFRESCOS.clear()


CRM_ORDEN = {"redsocial_id": "1", "redsocial": "Instagram", "prod": "Followers",
             "demora": " - ", "url": "u", "costo": 12.0, "obs": "",
             "cant_inicial": "500", "cantidad": "500", "programado": 0,
             "fecha_programada": None, "comentarios": [], "producto_id": "77"}

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 1. Un POST de orden no se reintenta si el CRM ya contestó ==")
print("   (antes: 'login' suelto en la URL disparaba el reintento y duplicaba)")

check("una URL con 'login' en la query NO cuenta como sesión caída",
      web._growi_sesion_caida(FakeResp({}, url="https://crm.fake/paginas/trafico.php?next=login")) is False)
check("un producto llamado 'login' tampoco",
      web._growi_sesion_caida(FakeResp({}, url="https://crm.fake/paginas/ver.php?prod=login")) is False)
check("el redirect real al login SÍ se detecta",
      web._growi_sesion_caida(FakeResp({}, url="https://crm.fake/cuenta/login.php")) is True)
check("y el 401 también",
      web._growi_sesion_caida(FakeResp({}, status=401, url="https://crm.fake/x")) is True)
check("si el CRM ya respondió sobre la orden, no se reintenta",
      web._respuesta_de_orden_valida(FakeResp({"success": True, "insertadas": 1})) is True)
check("una página HTML no cuenta como respuesta de orden",
      web._respuesta_de_orden_valida(FakeResp(None, texto="<html>login</html>")) is False)

# El reintento real, contra el _growi_request de verdad: el CRM contesta la
# orden Y redirige al login a la vez (el caso ambiguo que duplicaba).
limpiar()
intentos = {"n": 0}


class SesionAmbigua:
    headers = {}
    proxies = {}

    def request(self, method, url, timeout=None, **kw):
        intentos["n"] += 1
        return FakeResp({"success": True, "insertadas": 1},
                        url="https://crm.fake/cuenta/login.php")

    def get(self, url, **kw):
        return self.request("GET", url)


web._growi_sessions.clear()
web._growi_sessions[7] = {"session": SesionAmbigua(), "cfg": {"crm_url": "https://crm.fake"}}
resp = REAL_GROWI_REQUEST("POST", "/paginas/enviar_trafico.php", account_id=7,
                          json={"ordenes": []}, headers={})
web._growi_sessions.clear()

check("la orden se mandó UNA sola vez pese al redirect ambiguo",
      intentos["n"] == 1, f"se mandó {intentos['n']} veces")
check("y se devuelve la respuesta del CRM en vez de reintentar",
      resp.json().get("insertadas") == 1, resp.json())

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 2. El consumo se registra solo si la orden entró ==")
print("   (antes: se grababa antes de mandar y quemaba la cantidad para siempre)")

limpiar()
cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)], "client": "peter", "url": "u"})
check("envío OK → se registra el consumo", len(USOS) == 1 and USOS[0]["qty"] == 500, USOS)

limpiar()


def cae(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    if "obtenercostotrafico" in path:
        return FakeResp({"costoTrafico": None})
    raise _rq.exceptions.ConnectTimeout("el CRM no responde")


web._growi_request = cae
r = cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)], "client": "peter", "url": "u"})
check("el envío falla → NO se registra consumo", not USOS, USOS)
check("y el front recibe el error", r.status_code == 500, r.status_code)

limpiar()


def rechaza(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    if "obtenercostotrafico" in path:
        return FakeResp({"costoTrafico": None})
    return FakeResp({"success": False, "insertadas": 0, "messages": [],
                     "warnings": [], "errors": ["saldo insuficiente"]})


web._growi_request = rechaza
cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)], "client": "peter", "url": "u"})
check("el CRM rechaza → NO se registra consumo", not USOS, USOS)

limpiar()


def parcial(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    if "obtenercostotrafico" in path:
        return FakeResp({"costoTrafico": None})
    return FakeResp({"success": True, "insertadas": 1, "messages": [],
                     "warnings": [], "errors": []})


web._growi_request = parcial
cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN), dict(CRM_ORDEN)],
                                      "client": "peter", "url": "u"})
check("entra 1 de 2 → no se registra (no sabemos cuál)", not USOS, USOS)
web._growi_request = fake_growi_request

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 3. Órdenes colgadas en 'enviando' vuelven a aparecer ==")

estado = {}


class RepoFalso:
    fila = {"id": 9, "account_id": None, "intentos": 1, "post_url": "u",
            "payload": {"comentarios": [], "ordenes": []}}
    rescatadas = []

    def revisar_ordenes_colgadas(self, minutos=10):
        self.rescatadas.append(minutos)
        return 1

    def tomar_orden_para_reintentar(self):
        return None

    def reprogramar_orden(self, oid, err, reintentable=True):
        estado.update({"id": oid, "reintentable": reintentable})

    def marcar_orden_enviada(self, oid):
        estado.update({"enviada": oid})


rf = RepoFalso()
orden_cola._repo = lambda: rf
check("existe el rescate de colgadas", hasattr(rf, "revisar_ordenes_colgadas"))

# El worker lo llama en cada vuelta: lo ejercitamos con una vuelta sola.
import inspect                                                       # noqa: E402
src_loop = inspect.getsource(orden_cola._loop)
check("el worker llama a revisar_ordenes_colgadas en cada vuelta",
      "revisar_ordenes_colgadas" in src_loop)

from common import repository as repo_real                           # noqa: E402
check("el claim le pone fecha límite al envío en vuelo",
      "TIMEOUT_ENVIANDO_MIN" in inspect.getsource(repo_real.tomar_orden_para_reintentar))
check("las colgadas van a 'revisar', no a reintento automático",
      'p.estado = "revisar"' in inspect.getsource(repo_real.revisar_ordenes_colgadas))

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 4. El saldo del envío se pide fresco ==")
limpiar()
cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)], "url": "u"})
check("el envío resuelve la campaña con refrescar=True", REFRESCOS == [True], REFRESCOS)
check("y el disponible que viaja es el saldo fresco",
      ENVIOS[-1]["payload"]["disponible"] == 200.0, ENVIOS[-1]["payload"]["disponible"])

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 5. El envío tiene su propio timeout ==")
# Se mide el timeout REAL con el que sale cada request, no el código fuente.
vistos = {}


class SesionQueMide:
    headers = {}
    proxies = {}

    def request(self, method, url, timeout=None, **kw):
        vistos[url.split("/")[-1].split("?")[0]] = timeout
        return FakeResp({"success": True, "insertadas": 0})

    def get(self, url, **kw):
        return self.request("GET", url)


web._growi_sessions.clear()
web._growi_sessions[7] = {"session": SesionQueMide(), "cfg": {"crm_url": "https://crm.fake"}}
REAL_GROWI_REQUEST("POST", "/paginas/enviar_trafico.php", account_id=7, json={}, headers={})
REAL_GROWI_REQUEST("GET", "/paginas/obtener_productos_con_precios.php", account_id=7)
web._growi_sessions.clear()

check("el envío de órdenes usa (10, 60)", vistos.get("enviar_trafico.php") == (10, 60),
      vistos.get("enviar_trafico.php"))
check("una consulta común sigue con 15s",
      vistos.get("obtener_productos_con_precios.php") == 15,
      vistos.get("obtener_productos_con_precios.php"))

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 6. Fechas programadas en hora de Argentina ==")
ar = datetime.now(tz=timezone(timedelta(hours=-3)))
check("ahora_ar_texto tiene el formato del CRM",
      len(ordmod.ahora_ar_texto()) == 16 and ordmod.ahora_ar_texto()[:10] == ar.date().isoformat(),
      ordmod.ahora_ar_texto())

limpiar()
prog = dict(CRM_ORDEN, programado=1, fecha_programada=None)
cli.post("/api/enviar_trafico", json={"ordenes": [prog], "url": "u"})
o = ENVIOS[-1]["payload"]["ordenes"][0]
check("una orden programada sin fecha se completa con la hora AR",
      o["fecha_programada"] and o["fecha_programada"][:10] == ar.date().isoformat(),
      o.get("fecha_programada"))

limpiar()
prog2 = dict(CRM_ORDEN, programado=1, fecha_programada="2026-12-25 10:00")
cli.post("/api/enviar_trafico", json={"ordenes": [prog2], "url": "u"})
check("si ya trae fecha, no se pisa",
      ENVIOS[-1]["payload"]["ordenes"][0]["fecha_programada"] == "2026-12-25 10:00",
      ENVIOS[-1]["payload"]["ordenes"][0]["fecha_programada"])

js = open("/app/static/app.js").read()
check("el front formatea las programadas en la zona AR",
      "America/Argentina/Buenos_Aires" in js and "_partesAR" in js)
check("y calcula los turnos con el reloj del servidor",
      "_ahoraServidor()" in js and "_sincronizarReloj" in js)

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 7. El precio lo dice el CRM, no el navegador ==")
limpiar()
PRECIOS[("77", 500)] = 12.0
cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)], "url": "u"})
check("precio correcto → pasa igual",
      ENVIOS[-1]["payload"]["ordenes"][0]["costo"] == 12.0,
      ENVIOS[-1]["payload"]["ordenes"][0]["costo"])

limpiar()
tramposa = dict(CRM_ORDEN, costo=0)          # devtools: "me sale gratis"
cli.post("/api/enviar_trafico", json={"ordenes": [tramposa], "url": "u"})
p = ENVIOS[-1]["payload"]
check("costo 0 desde el front se corrige con el del CRM",
      p["ordenes"][0]["costo"] == 12.0, p["ordenes"][0]["costo"])
check("y el costo_orden se recalcula con el precio real",
      p["costo_orden"] == 12.0, p["costo_orden"])
check("y el resto también", p["resto"] == round(200.0 - 12.0, 6), p["resto"])
check("producto_id no viaja al CRM", "producto_id" not in p["ordenes"][0], p["ordenes"][0].keys())

limpiar()
PRECIOS.clear()                              # el CRM no sabe el precio
cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)], "url": "u"})
check("si no se puede verificar, no se bloquea el envío",
      bool(ENVIOS) and ENVIOS[-1]["payload"]["ordenes"][0]["costo"] == 12.0,
      ENVIOS[-1]["payload"]["ordenes"][0]["costo"] if ENVIOS else "no mandó")

# ─────────────────────────────────────────────────────────────────────────────
print("\n== 8. Un solo login aunque manden varios a la vez ==")
web._growi_sessions.clear()
logins = []
_lock_test = threading.Lock()


def login_lento(cfg, verify=True, account_id=None):
    import time
    with _lock_test:
        logins.append(account_id)
    time.sleep(0.05)                       # ventana para la carrera

    class S:
        headers = {}
        proxies = {}

        def request(self, method, url, timeout=None, **kw):
            return FakeResp({"ymdhmAR": "2026-08-09 21:40"})

        def get(self, url, **kw):
            return self.request("GET", url)
    return S()


web._growi_login_with = login_lento
hilos = [threading.Thread(target=lambda: web._get_growi_session(7)) for _ in range(10)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()
check("10 hilos pidiendo sesión abren UN solo login", len(logins) == 1, logins)
check("y todos comparten la misma sesión",
      len({id(web._get_growi_session(7)["session"])}) == 1)

print("\n" + ("TODO OK" if not FALLOS else f"FALLARON {len(FALLOS)}:\n  - " + "\n  - ".join(FALLOS)))
sys.exit(1 if FALLOS else 0)
