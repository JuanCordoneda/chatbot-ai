"""
El corte por saldo: una campaña sin plata NO manda la orden.

Lo que se afirma acá:
  - con saldo suficiente la orden sale (no rompimos el camino normal);
  - con saldo insuficiente NO sale NADA al CRM (enviar_trafico.php ni se llama)
    y el vendedor recibe motivo="sin_saldo" para poder elegir otra campaña;
  - una tanda de puros comentarios pasa aunque la campaña esté en cero: no
    consumen saldo;
  - si el saldo NO se pudo leer del CRM (None) no se corta: cortar contra el
    valor de respaldo del .env rebotaría órdenes buenas;
  - la orden cortada NO se encola: la cola la reintentaría contra la misma
    campaña sin plata hasta agotar los reintentos.
"""
import json
import sys

sys.path.insert(0, "/app")

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido

import app as web                                                   # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" +
          (f"\n         → {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


class FakeResp:
    def __init__(self, payload=None, status=200):
        self._payload, self.status_code = payload, status
        self.text = json.dumps(payload or {})
        self.url, self.headers = "https://crm.fake/x", {"Content-Type": "application/json"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


# Qué le llegó al CRM: si el corte funciona, enviar_trafico.php queda vacío.
ENVIADO = []


def base_ok(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-10 11:00"})
    if "obtenercostotrafico" in path:
        # El precio lo dice el CRM, no el navegador. El producto 94 son los
        # comentarios: salen 0 y por eso no tocan el saldo de la campaña.
        prod = str((kw.get("json") or {}).get("producto") or "")
        return FakeResp({"costoTrafico": 0 if prod == "94" else 0.715})
    if "enviar_trafico" in path:
        ENVIADO.append(kw.get("json"))
        return FakeResp({"success": True, "insertadas": 1, "messages": ["ok"],
                         "warnings": [], "errors": []})
    raise AssertionError(path)


web._growi_request = base_ok
web._log_uso = lambda *a, **kw: None
web._cuenta_tiene_crm_propio = lambda aid: True
web._account_crm_cfg = lambda aid: {"crm_url": "https://crm.fake", "crm_email": "v@d",
                                    "crm_password": "x", "crm_disponible": "150"}


def con_saldo(saldo):
    """Fija el saldo que 'tiene' la campaña de la que salen los fondos."""
    web.resolver_venta = lambda a, i, idventa_elegida=None, refrescar=False: {
        "idventa": "8811", "idvendedor": "1219", "origen": "auto",
        "detalle": "campaña", "saldo": saldo}


cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["stamp"] = web.SESSION_STAMP
    s["account_id"] = 7
    s["user_id"] = 21
    s["username"] = "tmignola"
    s["cred_key"] = web._guardar_credencial(7, "x")

# El costo REAL lo pone el CRM (obtenercostotrafico): 0.715 por orden, sin
# importar lo que diga el navegador. Los umbrales de abajo son contra ESE número.
ORDEN_T = {"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 0.715,
           "cant_inicial": "500", "cantidad": "500", "programado": 0,
           "fecha_programada": None, "comentarios": [], "producto_id": "1"}
COMS = ["mujeres:", "linda", "hombres:", "capo"]
ORDEN_C = {"tipo": "comentarios", "redsocialId": "1", "productoId": "94",
           "productoNombre": "Comentarios Reales Verificados", "link": "u",
           "costo": 0, "cantidad": 2, "cuando": "ahora", "comentarios": COMS}


def mandar_trafico():
    return cli.post("/api/enviar_trafico", json={"ordenes": [dict(ORDEN_T)], "url": "u",
                                                 "client": "lucydoughty"})


def mandar_comentarios():
    return cli.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                           "ordenes": [dict(ORDEN_C)],
                                           "client": "lucydoughty"})


print("\n=== 1. Campaña CON saldo: la orden sale ===")
ENVIADO.clear()
con_saldo(167.119)
r = mandar_trafico()
check("responde 200", r.status_code == 200, f"status {r.status_code}")
check("llegó al CRM", len(ENVIADO) == 1, f"{len(ENVIADO)} envíos")

print("\n=== 2. Campaña SIN saldo: no sale nada ===")
ENVIADO.clear()
con_saldo(0.0)
r = mandar_trafico()
d = r.get_json() or {}
check("NO llegó nada al CRM", not ENVIADO, f"{len(ENVIADO)} envíos: {ENVIADO}")
check("responde error", r.status_code == 500, f"status {r.status_code}")
check("motivo sin_saldo", d.get("motivo") == "sin_saldo", f"motivo {d.get('motivo')!r}")
check("NO se encoló", not d.get("encolada"), f"encolada={d.get('encolada')}")
msg = d.get("error") or ""
check("dice cuánto cuesta y cuánto queda", "0.72" in msg or "0.71" in msg, msg)
check("dice qué hacer", "campaña" in msg.lower(), msg)
for fuga in ["Traceback", "SaldoInsuficiente", "/paginas/", "object at 0x"]:
    check(f"sin fuga de infra ({fuga})", fuga not in msg, msg)
print(f"    mensaje: {msg}")

print("\n=== 3. Saldo que no alcanza (parcial) ===")
ENVIADO.clear()
con_saldo(0.5)                     # la orden cuesta 0.715
r = mandar_trafico()
check("NO llegó nada al CRM", not ENVIADO, f"{len(ENVIADO)} envíos")
check("motivo sin_saldo", (r.get_json() or {}).get("motivo") == "sin_saldo")

print("\n=== 4. Solo comentarios con campaña en cero: pasa ===")
ENVIADO.clear()
con_saldo(0.0)
r = mandar_comentarios()
check("responde 200", r.status_code == 200, f"status {r.status_code}")
check("llegó al CRM", len(ENVIADO) == 1, f"{len(ENVIADO)} envíos")

print("\n=== 5. Saldo ilegible (None): NO se corta ===")
ENVIADO.clear()
con_saldo(None)
r = mandar_trafico()
check("responde 200", r.status_code == 200, f"status {r.status_code}")
check("llegó al CRM", len(ENVIADO) == 1, f"{len(ENVIADO)} envíos")

print("\n" + ("=" * 60))
if FALLOS:
    print(f"FALLARON {len(FALLOS)}: " + ", ".join(FALLOS))
    sys.exit(1)
print("TODO OK")
