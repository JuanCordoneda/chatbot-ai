"""Verificación del envío de órdenes de COMENTARIOS.

Levanta la app real con un CRM falso enchufado en _growi_request, y comprueba
lo que efectivamente sale por el cable: credenciales/idvendedor de la cuenta,
fecha en hora AR, y los comentarios completos.
"""
import json
import os
import sys
import types
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")

# CINTURÓN DE SEGURIDAD: este test corre contra un entorno cuyo .env apunta al
# CRM de PRODUCCIÓN. Antes de importar nada de la app, se rompe toda salida HTTP
# real: si algún camino se escapa del stub, revienta en vez de cargar una orden
# de verdad en el CRM.
import requests as _rq                                  # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido
_rq.post = _prohibido
_rq.get = _prohibido

import app as web                                       # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" + (f" — {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


# ── CRM falso ────────────────────────────────────────────────────────────────
ENVIADO = {}


class FakeResp:
    def __init__(self, payload, status=200, texto=None):
        self._payload = payload
        self.status_code = status
        self.text = texto if texto is not None else json.dumps(payload)
        self.url = "https://crm.fake/paginas/enviar_trafico.php"
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
        return FakeResp({"success": True, "insertadas": 2, "messages": ["ok"],
                         "warnings": [], "errors": []})
    raise AssertionError(f"llamada inesperada al CRM: {path}")


web._growi_request = fake_growi_request
web._log_uso = lambda *a, **kw: None
# La cuenta tiene su CRM propio: la guarda multi-tenant se prueba a fondo en
# test_multicuenta.py, con un repositorio falso por cuenta.
web._cuenta_tiene_crm_propio = lambda aid: True
web._account_crm_cfg = lambda aid: {"crm_url": "https://crm.fake", "crm_email": "vende@dor",
                                    "crm_password": "x", "crm_disponible": "150"}
# La cuenta del vendedor tiene SU idvendedor y SU campaña.
web.resolver_venta = lambda account_id, ig, idventa_elegida=None, refrescar=False: {
    "idventa": "8811", "idvendedor": "77-TOMAS", "origen": "auto",
    "detalle": "campaña del cliente", "saldo": 120.5,
}

cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = 7
    s["user_id"] = 21
    s["username"] = "tomas"

COMS = ["mujeres:", "que linda", "diosa", "hombres:", "crack", "grande"]
ORDEN = {
    "tipo": "comentarios", "redsocialId": "1", "redsocial": "Instagram",
    "productoNombre": "Comentarios verificados", "link": "https://instagram.com/p/abc",
    "costo": 4.5, "cantidad": 4, "cuando": "ahora", "comentarios": COMS,
}

print("\n== 1. Envío OK de una orden de comentarios ==")
r = cli.post("/api/publicar", json={
    "url": "https://instagram.com/p/abc", "comentarios": COMS,
    "ordenes": [ORDEN], "client": "peter", "idventa": "8811",
})
body = r.get_json()
p = ENVIADO.get("payload") or {}

check("responde 200", r.status_code == 200, r.status_code)
check("usa la sesión CRM de la cuenta del vendedor", ENVIADO.get("account_id") == 7,
      ENVIADO.get("account_id"))
check("idvendedor = el del vendedor, NO el global del .env",
      p.get("idvendedor") == "77-TOMAS", p.get("idvendedor"))
check("creador = el del vendedor", p.get("creador") == "77-TOMAS", p.get("creador"))
check("idventa = la campaña resuelta, no la del .env", p.get("idventa") == "8811",
      p.get("idventa"))
check("fecha = la del CRM en hora AR", p.get("fecha") == "2026-08-09", p.get("fecha"))
check("manda 1 orden", len(p.get("ordenes") or []) == 1, len(p.get("ordenes") or []))

o = (p.get("ordenes") or [{}])[0]
check("orden normalizada a forma CRM (url/prod/cant_inicial)",
      o.get("url") == "https://instagram.com/p/abc" and o.get("prod") == "Comentarios verificados"
      and o.get("cant_inicial") == "4", o)
check("van los 6 renglones de comentarios", len(o.get("comentarios") or []) == 6,
      o.get("comentarios"))
check("no se pierde ningún comentario al barajar",
      sorted(o.get("comentarios") or []) == sorted(COMS), o.get("comentarios"))
check("los headers de género quedan en su lugar",
      (o["comentarios"][0] == "mujeres:" and o["comentarios"][3] == "hombres:"),
      o.get("comentarios"))
check("disponible = saldo real de la campaña", p.get("disponible") == 120.5, p.get("disponible"))
check("resto = disponible - costo", p.get("resto") == round(120.5 - 4.5, 6), p.get("resto"))
check("el front recibe insertadas", (body.get("resultado") or {}).get("insertadas") == 2, body)
check("el front recibe ok=True", (body.get("resultado") or {}).get("ok") is True, body)
check("el informe trae los comentarios", "que linda" in (body.get("informe") or ""), body.get("informe"))

print("\n== 2. Dos órdenes (verificados + no verificados) con listas distintas ==")
ENVIADO.clear()
A = ["uno", "dos"]
B = ["tres", "cuatro", "cinco"]
r = cli.post("/api/publicar", json={
    "url": "https://instagram.com/p/abc", "comentarios": A + B,
    "ordenes": [
        {**ORDEN, "productoNombre": "Verificados", "comentarios": A, "cantidad": 2},
        {**ORDEN, "productoNombre": "No verificados", "comentarios": B, "cantidad": 3},
    ],
    "client": "peter",
})
ords = (ENVIADO.get("payload") or {}).get("ordenes") or []
check("salen las 2 órdenes", len(ords) == 2, len(ords))
check("cada una con SU lista",
      sorted(ords[0]["comentarios"]) == sorted(A) and sorted(ords[1]["comentarios"]) == sorted(B),
      [o.get("comentarios") for o in ords])

print("\n== 3. Fecha AR cuando el CRM no contesta la hora ==")
ENVIADO.clear()


def sin_hora(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        raise ConnectionError("CRM sin responder la hora")
    return fake_growi_request(method, path, account_id=account_id, **kw)


web._growi_request = sin_hora
r = cli.post("/api/publicar", json={"url": "u", "comentarios": COMS, "ordenes": [ORDEN]})
esperada = datetime.now(tz=timezone(timedelta(hours=-3))).date().isoformat()
check("cae a la fecha AR local (no UTC)",
      (ENVIADO.get("payload") or {}).get("fecha") == esperada,
      (ENVIADO.get("payload") or {}).get("fecha"))

print("\n== 4. Se cayó la red ANTES de mandar → se encola ==")
ENVIADO.clear()
encoladas = []
web._repo = types.SimpleNamespace(
    encolar_orden=lambda post_url, payload, **kw: (
        encoladas.append({"post_url": post_url, "payload": payload, **kw}) or {"id": 55}),
)


def cae_al_conectar(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    import requests as rq
    raise rq.exceptions.ConnectTimeout("no se pudo abrir el socket")


web._growi_request = cae_al_conectar
r = cli.post("/api/publicar", json={"url": "https://instagram.com/p/abc",
                                    "comentarios": COMS, "ordenes": [ORDEN],
                                    "client": "peter"})
res = (r.get_json() or {}).get("resultado") or {}
check("responde 200 (no rompe la pantalla)", r.status_code == 200, r.status_code)
check("queda encolada", res.get("encolada") is True, res)
check("con id de la cola", res.get("encolada_id") == 55, res)
check("la cola guarda el account_id del vendedor",
      encoladas and encoladas[0].get("account_id") == 7, encoladas)
check("la cola guarda las órdenes ya normalizadas",
      encoladas and encoladas[0]["payload"]["ordenes"][0].get("url") == "https://instagram.com/p/abc",
      encoladas)
check("el mensaje avisa que se reenvía sola",
      "cola" in (res.get("errors") or [""])[0], res.get("errors"))

print("\n== 5. Se cortó ESPERANDO la respuesta → NO se encola (podría duplicar) ==")
encoladas.clear()


def corta_leyendo(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    import requests as rq
    raise rq.exceptions.ReadTimeout("se cortó esperando")


web._growi_request = corta_leyendo
r = cli.post("/api/publicar", json={"url": "u", "comentarios": COMS, "ordenes": [ORDEN]})
res = (r.get_json() or {}).get("resultado") or {}
check("NO se encola", res.get("encolada") is False and not encoladas, (res, encoladas))
check("avisa que hay que revisar en Growi",
      "Revisá en Growi" in (res.get("errors") or [""])[0], res.get("errors"))

print("\n== 6. El CRM rechaza la orden ==")
web._growi_request = lambda method, path, account_id=None, **kw: (
    FakeResp({"ymdhmAR": "2026-08-09 21:40"}) if "server_time_ar" in path
    else FakeResp({"success": False, "insertadas": 0, "messages": [],
                   "warnings": [], "errors": ["saldo insuficiente"]}))
r = cli.post("/api/publicar", json={"url": "u", "comentarios": COMS, "ordenes": [ORDEN]})
res = (r.get_json() or {}).get("resultado") or {}
check("ok=False", res.get("ok") is False, res)
check("el error del CRM llega al vendedor", res.get("errors") == ["saldo insuficiente"], res)

print("\n== 7. El reintento de la cola sale con las creds de SU cuenta ==")
ENVIADO.clear()
web._growi_request = fake_growi_request
crm = web._enviar_de_cola({
    "id": 55, "account_id": 7, "user_id": 21,
    "post_url": "https://instagram.com/p/abc", "client_ig_username": "peter",
    "payload": {"comentarios": COMS, "ordenes": [
        web._ordenes.normalizar_orden(ORDEN, 0, COMS)], "disponible": None},
})
p = ENVIADO.get("payload") or {}
check("el reintento usa la sesión de la cuenta 7", ENVIADO.get("account_id") == 7,
      ENVIADO.get("account_id"))
check("el reintento lleva el idvendedor del vendedor", p.get("idvendedor") == "77-TOMAS",
      p.get("idvendedor"))
check("devuelve la respuesta del CRM", crm.get("success") is True, crm)

print("\n== 8. El camino viejo del openAIService quedó cortado ==")
import re as _re
src = open("/app/app.py").read()
check("webService ya no llama a openai-service /publicar",
      "OPENAI_SERVICE_URL}/publicar" not in src)
front = open("/app/static/app.js").read()
check("el front sigue pegándole a /api/publicar", '"/api/publicar"' in front)

print("\n" + ("TODO OK" if not FALLOS else f"FALLARON {len(FALLOS)}: {FALLOS}"))
sys.exit(1 if FALLOS else 0)
