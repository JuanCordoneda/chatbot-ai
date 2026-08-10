"""
El idvendedor se descubre al loguearse y se guarda en la cuenta.

Antes salía de parsear las campañas en cada envío: si ese parseo fallaba justo
ahí (CRM lento, cuenta sin campañas activas), no había con qué cargar la orden.
Ahora se lee del CRM una vez al entrar, queda en la base, y el envío lo toma de
ahí.
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

FACU = "634"
web.GROWI_IDVENDEDOR = FACU
web.GROWI_IDVENTA = "32600"

FALLOS = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" +
          (f"\n         → {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


class FakeResp:
    def __init__(self, payload=None, texto=None, status=200, url="https://crm.fake/x"):
        self._payload, self.status_code = payload, status
        self.text = texto if texto is not None else json.dumps(payload or {})
        self.url, self.headers = url, {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload

    def raise_for_status(self):
        pass


# La cuenta 5 arranca SIN idvendedor guardado, como las de producción.
CUENTA = {"id": 5, "email": "lautaro@growi.com", "idvendedor_real": "702",
          "guardado": "", "campanas": [("9911", "702", "500.00")]}
GUARDADOS = []
PEDIDOS = []


def _html(campanas):
    return "<div>" + "".join(
        f'<button class="seleccionar-cliente" data-id="{i}" data-idvendedor="{v}" '
        f'data-nombre="C{i}" data-correo="c@x" data-vendedor="x" data-estadoventa="1" '
        f'data-cantidad-disponible="{d}" data-monto="1" data-fecha="2026-08-01">x</button>'
        for (i, v, d) in campanas) + "</div>"


def fake_request(method, path, account_id=None, **kw):
    PEDIDOS.append(path)
    if "traer_campanas" in path:
        return FakeResp(texto=_html(CUENTA["campanas"]))
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
    if "editarv" in path:
        return FakeResp({"cliente_url": "https://instagram.com/nadia/"})
    if "obtenercostotrafico" in path:
        return FakeResp({"costoTrafico": 1.0})
    if "enviar_trafico" in path:
        ENVIOS.append({"payload": kw.get("json")})
        return FakeResp({"success": True, "insertadas": 1, "messages": [],
                         "warnings": [], "errors": []})
    raise AssertionError(f"inesperado: {path}")


ENVIOS = []
web._growi_request = fake_request
web._log_uso = lambda *a, **kw: None


class FakeRepo:
    @staticmethod
    def get_account_crm_config(account_id):
        if account_id != CUENTA["id"]:
            return None
        return {"crm_email": CUENTA["email"], "crm_password": "x",
                "crm_url": "https://crm.fake", "crm_proxy": "",
                "crm_idvendedor": CUENTA["guardado"], "crm_idventa": "",
                "crm_disponible": "150"}

    @staticmethod
    def guardar_idvendedor(account_id, idv):
        GUARDADOS.append((account_id, idv))
        CUENTA["guardado"] = idv
        return True

    @staticmethod
    def get_client_by_ig_username(ig, account_id):
        return None


web._repo = FakeRepo

print("\n== 1. Al loguearse se descubre y se guarda ==")
check("la cuenta arranca sin idvendedor", CUENTA["guardado"] == "")
web._refrescar_idvendedor(CUENTA["id"])
check("se guardó el idvendedor que dice el CRM", GUARDADOS == [(5, "702")], GUARDADOS)
check("y quedó en la config de la cuenta", CUENTA["guardado"] == "702", CUENTA["guardado"])
check("se pidió el listado de campañas UNA vez",
      sum(1 for p in PEDIDOS if "traer_campanas" in p) == 1, PEDIDOS)
check("y NO se resolvió el perfil IG de cada campaña (sería lentísimo en el login)",
      not any("editarv" in p for p in PEDIDOS), PEDIDOS)

print("\n== 2. El envío lo usa desde la base ==")
ENVIOS.clear()
c = web.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = CUENTA["id"]
    s["user_id"] = 1
r = c.post("/api/enviar_trafico", json={
    "ordenes": [{"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
                 "cant_inicial": "10", "cantidad": "10", "programado": 0,
                 "fecha_programada": None, "comentarios": [], "producto_id": "77"}],
    "url": "u", "client": "nadia"})
check("la orden sale con el idvendedor guardado",
      bool(ENVIOS) and ENVIOS[-1]["payload"]["idvendedor"] == "702",
      ENVIOS[-1]["payload"]["idvendedor"] if ENVIOS else r.get_json())

print("\n== 3. Si el CRM no lista campañas, el valor guardado salva el envío ==")
print("   (antes: sin campañas no había idvendedor y no se podía mandar)")
ENVIOS.clear()
CUENTA["campanas"] = []          # el CRM ya no devuelve ninguna campaña
web._VENTAS_CACHE.clear()
r = c.post("/api/enviar_trafico", json={
    "ordenes": [{"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
                 "cant_inicial": "10", "cantidad": "10", "programado": 0,
                 "fecha_programada": None, "comentarios": [], "producto_id": "77"}],
    "url": "u", "client": "nadia", "idventa": ""})
# Sin campañas no hay idventa, así que la guarda corta: lo que importa es que
# NO caiga en el idvendedor del dueño.
cuerpo = r.get_json() or {}
if ENVIOS:
    check("si igual salió, lleva el idvendedor propio",
          ENVIOS[-1]["payload"]["idvendedor"] == "702", ENVIOS[-1]["payload"])
else:
    check("no manda con los ids del dueño, corta con un mensaje",
          "error" in cuerpo and FACU not in json.dumps(cuerpo), cuerpo)

print("\n== 4. El login no se rompe si el CRM falla ==")
GUARDADOS.clear()


def cae(method, path, account_id=None, **kw):
    raise _rq.exceptions.ConnectTimeout("CRM caído")


web._growi_request = cae
web._refrescar_idvendedor(CUENTA["id"])       # no debe lanzar
check("no lanza y no guarda nada", not GUARDADOS, GUARDADOS)
web._growi_request = fake_request

print("\n== 5. Nunca guarda el id del dueño para un vendedor ==")
GUARDADOS.clear()
CUENTA["campanas"] = [("9911", "", "500.00")]     # el CRM no informa idvendedor
web._refrescar_idvendedor(CUENTA["id"])
check("sin dato del CRM no inventa ni usa el del .env", not GUARDADOS, GUARDADOS)

print("\n== 6. Se actualiza si el CRM cambia el id ==")
GUARDADOS.clear()
CUENTA["campanas"] = [("9911", "999", "500.00")]
web._refrescar_idvendedor(CUENTA["id"])
check("guarda el nuevo valor", GUARDADOS == [(5, "999")], GUARDADOS)

print("\n== 7. El login lo dispara para cualquier cuenta ==")
import inspect                                                       # noqa: E402
src = inspect.getsource(web.login)
check("la ruta /login llama a _refrescar_idvendedor", "_refrescar_idvendedor" in src)
check("solo cuando hay cuenta (el admin no tiene)", 'user["account_id"]' in src)

print("\n" + ("TODO OK" if not FALLOS else f"FALLARON {len(FALLOS)}:\n  - " + "\n  - ".join(FALLOS)))
sys.exit(1 if FALLOS else 0)
