"""No-regresión de /api/enviar_trafico (followers, likes, etc).

Ese camino YA andaba: lo único que cambió es que ahora comparte el armado del
payload con los comentarios. Este test fija el contrato de lo que sale al CRM.
"""
import json
import sys

sys.path.insert(0, "/app")

import requests as _rq                                  # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido

import app as web                                       # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" + (f" — {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


ENVIADO = {}


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)
        self.url = "https://crm.fake/x"
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def fake_growi_request(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    if "enviar_trafico" in path:
        ENVIADO["account_id"] = account_id
        ENVIADO["payload"] = kw.get("json")
        ENVIADO["headers"] = kw.get("headers")
        return FakeResp({"success": True, "insertadas": 1,
                         "messages": ["listo"], "warnings": [], "errors": []})
    raise AssertionError(f"llamada inesperada: {path}")


web._growi_request = fake_growi_request
web._log_uso = lambda *a, **kw: None
# La cuenta tiene su CRM propio: la guarda multi-tenant se prueba a fondo en
# test_multicuenta.py, con un repositorio falso por cuenta.
web._cuenta_tiene_crm_propio = lambda aid: True
web._account_crm_cfg = lambda aid: {"crm_url": "https://crm.fake", "crm_email": "v@d",
                                    "crm_password": "x", "crm_disponible": "150"}
web.resolver_venta = lambda account_id, ig, idventa_elegida=None, refrescar=False: {
    "idventa": "8811", "idvendedor": "77-TOMAS", "origen": "auto",
    "detalle": "campaña", "saldo": 200.0,
}

cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = 7
    s["user_id"] = 21
    s["username"] = "tomas"

# Órdenes tal cual las arma el front (ya en forma CRM).
CRM_ORDEN = {
    "redsocial_id": "1", "redsocial": "Instagram", "prod": "Followers",
    "demora": " - ", "url": "https://instagram.com/p/abc", "costo": 12.0,
    "obs": "", "cant_inicial": "500", "cantidad": "500",
    "programado": 0, "fecha_programada": None, "comentarios": [],
}

print("\n== Tráfico: envío normal ==")
r = cli.post("/api/enviar_trafico", json={
    "ordenes": [dict(CRM_ORDEN)], "costo_total": 12.0, "client": "peter",
    "idventa": "8811", "url": "https://instagram.com/p/abc",
})
p = ENVIADO.get("payload") or {}
check("responde 200", r.status_code == 200, r.status_code)
check("el front recibe el JSON del CRM tal cual",
      r.get_json() == {"success": True, "insertadas": 1, "messages": ["listo"],
                       "warnings": [], "errors": []}, r.get_json())
check("idvendedor de la cuenta", p.get("idvendedor") == "77-TOMAS", p.get("idvendedor"))
check("idventa resuelta", p.get("idventa") == "8811", p.get("idventa"))
check("fecha AR del CRM", p.get("fecha") == "2026-08-09", p.get("fecha"))
check("aprobada/vendedor/cant_enviada como siempre",
      p.get("aprobada") == "Aprobado" and p.get("vendedor") == " " and p.get("cant_enviada") == 0, p)
check("disponible = saldo de la campaña", p.get("disponible") == 200.0, p.get("disponible"))
check("costo_orden = suma de costos", p.get("costo_orden") == 12.0, p.get("costo_orden"))
check("resto = disponible - costo", p.get("resto") == 188.0, p.get("resto"))
check("la orden llega intacta",
      p["ordenes"][0]["prod"] == "Followers" and p["ordenes"][0]["cantidad"] == "500", p.get("ordenes"))
check("cada orden lleva el disponible", p["ordenes"][0]["disponible"] == 200.0, p["ordenes"][0])
check("headers del CRM (referer + XHR)",
      (ENVIADO["headers"] or {}).get("x-requested-with") == "XMLHttpRequest"
      and "trafico.php" in (ENVIADO["headers"] or {}).get("referer", ""), ENVIADO.get("headers"))
check("las órdenes de tráfico NO llevan comentarios",
      p["ordenes"][0]["comentarios"] == [], p["ordenes"][0])

print("\n== Tráfico: sin órdenes ==")
r = cli.post("/api/enviar_trafico", json={"ordenes": []})
check("400 con mensaje", r.status_code == 400 and "Sin órdenes" in r.get_json()["error"],
      (r.status_code, r.get_json()))

print("\n== Tráfico: el CRM falla ==")


def cae(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    raise _rq.exceptions.ConnectTimeout("proxy caído")


web._growi_request = cae
r = cli.post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)], "url": "u"})
check("500 con el error para el front",
      r.status_code == 500 and "error" in (r.get_json() or {}), (r.status_code, r.get_json()))

print("\n" + ("TODO OK" if not FALLOS else f"FALLARON {len(FALLOS)}: {FALLOS}"))
sys.exit(1 if FALLOS else 0)
