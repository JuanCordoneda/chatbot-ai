"""
La campaña con la que la orden ENTRÓ queda preseteada en la ficha del cliente.

El problema: la campaña asignada a mano se vence (se queda sin saldo o el CRM
deja de listarla) y a partir de ahí TODOS los envíos de ese cliente rebotan. El
vendedor elige otra en el momento, la orden sale, pero la ficha sigue apuntando
a la vieja y el siguiente envío vuelve a rebotar.

Lo que se afirma acá:
  - si el vendedor manda una campaña DISTINTA de la que le tocaba, se guarda;
  - la preselección del paso de órdenes (que manda la misma campaña que ya se
    resolvía sola) NO se guarda: pinearla convertiría en fijo a un cliente que
    hoy sigue solo la última campaña de su perfil;
  - si la campaña asignada ya no existe en el CRM y se usó la última del perfil,
    se repisa el preseteo muerto;
  - si el CRM rechaza la orden no se guarda nada: preseteamos campañas que
    sabemos que funcionan, no las que estamos por probar;
  - un post sin cliente no pinea nada (no hay ficha a la que guardarle).
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


ENVIADO = []
# Lo que el CRM contesta al envío. El caso 5 lo cambia a un rechazo.
RESPUESTA_CRM = {"success": True, "insertadas": 1, "messages": ["ok"],
                 "warnings": [], "errors": []}


def crm_fake(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-10 11:00"})
    if "obtenercostotrafico" in path:
        return FakeResp({"costoTrafico": 0.715})
    if "enviar_trafico" in path:
        ENVIADO.append(kw.get("json"))
        return FakeResp(dict(RESPUESTA_CRM))
    raise AssertionError(path)


web._growi_request = crm_fake
web._log_uso = lambda *a, **kw: None
web._cuenta_tiene_crm_propio = lambda aid: True
web._account_crm_cfg = lambda aid: {"crm_url": "https://crm.fake", "crm_email": "v@d",
                                    "crm_password": "x", "crm_disponible": "150",
                                    "crm_idvendedor": "1219", "crm_idventa": "1"}

# Campañas que "tiene" la cuenta en el CRM. La 8811 es la vieja del cliente y la
# 9922 la nueva; ambas del mismo perfil de IG.
VENTAS = [
    {"idventa": "8811", "idvendedor": "1219", "nombre": "vieja", "disponible": "0.00",
     "fecha": "2026-06-01", "ig_username": "lucydoughty", "activa": False},
    {"idventa": "9922", "idvendedor": "1219", "nombre": "nueva", "disponible": "500.00",
     "fecha": "2026-08-01", "ig_username": "lucydoughty", "activa": True},
]
web._traer_ventas = lambda account_id, usar_cache=True: [dict(v) for v in VENTAS]


class RepoFake:
    """Solo lo que toca este camino: leer la ficha del cliente y presetearle la
    campaña. El resto del repo real sigue estando (se delega por __getattr__)."""

    def __init__(self, real):
        self._real = real
        self.ficha = {"ig_username": "lucydoughty", "crm_idventa": "", "crm_idvendedor": ""}
        self.guardadas = []

    def __getattr__(self, n):
        return getattr(self._real, n)

    def get_client_by_ig_username(self, ig, account_id=None):
        return dict(self.ficha) if ig == "lucydoughty" else None

    def fijar_venta_de_cliente(self, account_id, ig, idventa, idvendedor=None):
        self.guardadas.append((account_id, ig, idventa))
        if (self.ficha["crm_idventa"] or "") == idventa:
            return False
        self.ficha["crm_idventa"] = idventa
        return True


repo = RepoFake(web._repo)
web._repo = repo

cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["stamp"] = web.SESSION_STAMP
    s["account_id"] = 7
    s["user_id"] = 21
    s["username"] = "tmignola"
    s["cred_key"] = web._guardar_credencial(7, "x")

ORDEN = {"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 0.715,
         "cant_inicial": "500", "cantidad": "500", "programado": 0,
         "fecha_programada": None, "comentarios": [], "producto_id": "1"}


def mandar(idventa="", client="lucydoughty"):
    ENVIADO.clear()
    repo.guardadas.clear()
    return cli.post("/api/enviar_trafico",
                    json={"ordenes": [dict(ORDEN)], "url": "u",
                          "client": client, "idventa": idventa})


print("\n=== 1. El vendedor cambia la campaña: queda preseteada ===")
repo.ficha["crm_idventa"] = "8811"          # la asignada, sin saldo
r = mandar(idventa="9922")                  # elige la nueva a mano
check("responde 200", r.status_code == 200, f"status {r.status_code}")
check("salió con la 9922", ENVIADO and ENVIADO[0]["idventa"] == "9922",
      json.dumps(ENVIADO[:1])[:200])
check("quedó guardada en la ficha", repo.ficha["crm_idventa"] == "9922",
      f"ficha {repo.ficha['crm_idventa']!r}")

print("\n=== 2. Segundo envío: ya sale solo con la nueva ===")
r = mandar()                                # sin elegir nada
check("salió con la 9922", ENVIADO and ENVIADO[0]["idventa"] == "9922",
      json.dumps(ENVIADO[:1])[:200])
check("no reescribe (es la misma)", not repo.guardadas, f"{repo.guardadas}")

print("\n=== 3. Preselección del front: NO pinea al cliente sin asignación ===")
repo.ficha["crm_idventa"] = ""              # cliente que resuelve solo
# El paso de órdenes preselecciona la campaña resuelta y la manda de vuelta:
# es la MISMA que auto elegiría (la última del perfil), así que no es un cambio.
r = mandar(idventa="9922")
check("responde 200", r.status_code == 200, f"status {r.status_code}")
check("la ficha sigue sin campaña fija", repo.ficha["crm_idventa"] == "",
      f"ficha {repo.ficha['crm_idventa']!r}")
check("no se llamó al guardado", not repo.guardadas, f"{repo.guardadas}")

print("\n=== 4. Campaña asignada que ya no existe: se repisa con la que entró ===")
repo.ficha["crm_idventa"] = "7000"          # no está en el listado del CRM
r = mandar()
check("responde 200", r.status_code == 200, f"status {r.status_code}")
check("salió con la última del perfil", ENVIADO and ENVIADO[0]["idventa"] == "9922",
      json.dumps(ENVIADO[:1])[:200])
check("la ficha ya no apunta a la muerta", repo.ficha["crm_idventa"] == "9922",
      f"ficha {repo.ficha['crm_idventa']!r}")

print("\n=== 5. El CRM rechaza: no se presetea nada ===")
RESPUESTA_CRM = {"success": False, "insertadas": 0, "messages": [],
                 "warnings": [], "errors": ["no entró"]}
repo.ficha["crm_idventa"] = "8811"
r = mandar(idventa="9922")
check("la ficha queda como estaba", repo.ficha["crm_idventa"] == "8811",
      f"ficha {repo.ficha['crm_idventa']!r}")
check("no se llamó al guardado", not repo.guardadas, f"{repo.guardadas}")
RESPUESTA_CRM = {"success": True, "insertadas": 1, "messages": ["ok"],
                 "warnings": [], "errors": []}

print("\n=== 6. Post sin cliente: no hay ficha que pinear ===")
repo.ficha["crm_idventa"] = "8811"
r = mandar(idventa="9922", client="")
check("responde 200", r.status_code == 200, f"status {r.status_code}")
check("no se llamó al guardado", not repo.guardadas, f"{repo.guardadas}")
check("la ficha del otro cliente intacta", repo.ficha["crm_idventa"] == "8811",
      f"ficha {repo.ficha['crm_idventa']!r}")

print("\n" + ("=" * 60))
if FALLOS:
    print(f"FALLARON {len(FALLOS)}: " + ", ".join(FALLOS))
    sys.exit(1)
print("TODO OK")
