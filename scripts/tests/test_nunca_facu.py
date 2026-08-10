"""
CACERÍA: que NUNCA una orden de otro vendedor termine en la cuenta de facu.

No alcanza con que la SESIÓN del CRM sea la del vendedor: el payload lleva
`idvendedor` e `idventa`, y si esos caen a los valores del .env la orden queda
cargada a nombre de facu y descontada de SU campaña, aunque haya entrado por la
sesión correcta. Este suite mete a cada cuenta en todas las condiciones
degradadas que se me ocurren y afirma lo mismo siempre:

    ninguna orden de un vendedor sale con idvendedor 634 ni idventa 32600.

(634 / 32600 son los valores del .env de producción: los de facu.)
"""
import json
import sys
import threading

sys.path.insert(0, "/app")

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido

import app as web                                                   # noqa: E402

# Los valores de facu, tal como están en el .env de prod.
FACU_VENDEDOR = "634"
FACU_VENTA = "32600"
web.GROWI_IDVENDEDOR = FACU_VENDEDOR
web.GROWI_IDVENTA = FACU_VENTA
web.GROWI_CRM_EMAIL = "FRigonatto@growi.com"

FALLOS = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" +
          (f"\n         → {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


ENVIOS = []
_lock = threading.Lock()

# Cuentas de vendedores. NINGUNA es facu. Cada una en un estado distinto de
# "mal configurada", que es donde vive el peligro.
CUENTAS = {
    1: {"email": "tomas@growi.com", "idvendedor": "701", "idventa": "8811",
        "ventas": [("8811", "701", "300.00", "peter")], "desc": "completa"},
    2: {"email": "lautaro@growi.com", "idvendedor": "", "idventa": "9911",
        "ventas": [("9911", "702", "500.00", "nadia")], "desc": "sin crm_idvendedor"},
    3: {"email": "juli@growi.com", "idvendedor": "703", "idventa": "",
        "ventas": [("7711", "703", "80.00", "cami")], "desc": "sin crm_idventa"},
    4: {"email": "nico@growi.com", "idvendedor": "", "idventa": "",
        "ventas": [], "desc": "sin ids y sin campañas"},
    5: {"email": "sofi@growi.com", "idvendedor": "705", "idventa": "5511",
        "ventas": "EXPLOTA", "desc": "el CRM falla al listar campañas"},
}

FALLAR_CAMPANAS = set()


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


def _html(ventas):
    return "<div>" + "".join(
        f'<button class="seleccionar-cliente" data-id="{i}" data-idvendedor="{v}" '
        f'data-nombre="Camp {i}" data-correo="c@x" data-vendedor="x" '
        f'data-estadoventa="1" data-cantidad-disponible="{d}" data-monto="1000" '
        f'data-fecha="2026-08-01">x</button>' for (i, v, d, _ig) in ventas) + "</div>"


class FakeSession:
    def __init__(self, account_id, email):
        self.account_id = account_id
        self.email = email
        self.headers = {}
        self.proxies = {}

    def request(self, method, url, timeout=None, **kw):
        if "server_time_ar" in url:
            return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
        if "traer_campanas" in url:
            cta = CUENTAS.get(self.account_id)
            if not cta or cta["ventas"] == "EXPLOTA" or self.account_id in FALLAR_CAMPANAS:
                raise _rq.exceptions.ConnectTimeout("el CRM no lista campañas")
            return FakeResp(texto=_html(cta["ventas"]))
        if "editarv.php" in url:
            idv = url.split("idv=")[-1]
            cta = CUENTAS.get(self.account_id) or {}
            ventas = cta.get("ventas") if isinstance(cta.get("ventas"), list) else []
            ig = next((v[3] for v in ventas if v[0] == idv), "")
            return FakeResp({"cliente_url": f"https://instagram.com/{ig}/"})
        if "enviar_trafico" in url:
            with _lock:
                ENVIOS.append({"account_id": self.account_id, "email_sesion": self.email,
                               "payload": kw.get("json")})
            return FakeResp({"success": True, "insertadas": 1, "messages": [],
                             "warnings": [], "errors": []})
        raise AssertionError(f"llamada inesperada: {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def fake_login_with(cfg, verify=True, account_id=None):
    email = cfg.get("crm_email") or "(env)"
    aid = next((a for a, c in CUENTAS.items() if c["email"] == email), None)
    return FakeSession(aid if aid is not None else 0,
                       email if aid is not None else f"ENV::{email}")


web._growi_login_with = fake_login_with
web._log_uso = lambda *a, **kw: None


class FakeRepo:
    @staticmethod
    def get_account_crm_config(account_id):
        c = CUENTAS.get(account_id)
        if not c:
            return None
        return {"crm_email": c["email"], "crm_password": "x",
                "crm_url": "https://crm.fake", "crm_proxy": "",
                "crm_idvendedor": c["idvendedor"], "crm_idventa": c["idventa"],
                "crm_disponible": "150"}

    @staticmethod
    def get_client_by_ig_username(ig, account_id):
        # El cliente tiene asignada a mano una campaña que YA NO EXISTE: otro
        # camino por el que resolver_venta cae al default.
        if ig == "fantasma":
            return {"crm_idventa": "00000", "crm_idvendedor": ""}
        return None

    encolar_orden = staticmethod(lambda post_url, payload, **kw: {"id": 1})


web._repo = FakeRepo


def limpiar():
    web._growi_sessions.clear()
    web._VENTAS_CACHE.clear()
    web._VENTA_IG_CACHE.clear()
    ENVIOS.clear()


def cli_de(aid):
    c = web.app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["account_id"] = aid
        s["user_id"] = 100 + (aid or 0)
        s["username"] = f"v{aid}"
    return c


COMS = ["mujeres:", "linda", "diosa", "hombres:", "crack"]
ORDEN = {"tipo": "comentarios", "redsocialId": "1", "redsocial": "Instagram",
         "productoNombre": "Comentarios", "link": "https://instagram.com/p/abc",
         "costo": 4.5, "cantidad": 3, "cuando": "ahora", "comentarios": list(COMS)}
CRM_ORDEN = {"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
             "cant_inicial": "10", "cantidad": "10", "programado": 0,
             "fecha_programada": None, "comentarios": [], "demora": " - ", "obs": ""}


def sin_rastro_de_facu(envios, contexto):
    """La afirmación central: ninguna orden de un vendedor lleva los ids de facu
    ni salió por su sesión."""
    malos = [e for e in envios
             if str((e["payload"] or {}).get("idvendedor")) == FACU_VENDEDOR
             or str((e["payload"] or {}).get("idventa")) == FACU_VENTA
             or str((e["payload"] or {}).get("creador")) == FACU_VENDEDOR
             or e["email_sesion"].startswith("ENV::")]
    check(f"{contexto}: ninguna orden cayó en la cuenta de facu", not malos,
          [{"cuenta": m["account_id"], "sesion": m["email_sesion"],
            "idvendedor": m["payload"].get("idvendedor"),
            "idventa": m["payload"].get("idventa")} for m in malos])


# ── 1. Cada cuenta mal configurada, comentarios ──────────────────────────────
print("\n== 1. Comentarios desde cuentas con configuración incompleta ==")
for aid, cta in CUENTAS.items():
    limpiar()
    r = cli_de(aid).post("/api/publicar", json={
        "url": "https://instagram.com/p/abc", "comentarios": COMS,
        "ordenes": [dict(ORDEN)], "client": "peter"})
    res = (r.get_json() or {}).get("resultado") or {}
    estado = "mandó" if ENVIOS else "no mandó"
    print(f"  · cuenta {aid} ({cta['desc']}): {estado}"
          + (f" idvendedor={ENVIOS[-1]['payload'].get('idvendedor')} "
             f"idventa={ENVIOS[-1]['payload'].get('idventa')}" if ENVIOS else
             f" — {(res.get('errors') or [''])[0][:70]}"))
    sin_rastro_de_facu(ENVIOS, f"cuenta {aid} ({cta['desc']})")

# ── 2. Lo mismo para tráfico ────────────────────────────────────────────────
print("\n== 2. Tráfico desde las mismas cuentas ==")
for aid, cta in CUENTAS.items():
    limpiar()
    cli_de(aid).post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)],
                                                  "client": "peter", "url": "u"})
    sin_rastro_de_facu(ENVIOS, f"tráfico cuenta {aid} ({cta['desc']})")

# ── 3. El listado de campañas se cae para TODAS ─────────────────────────────
print("\n== 3. El CRM no responde el listado de campañas (para todas) ==")
limpiar()
FALLAR_CAMPANAS.update(CUENTAS.keys())
for aid in CUENTAS:
    cli_de(aid).post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                            "ordenes": [dict(ORDEN)], "client": "peter"})
FALLAR_CAMPANAS.clear()
sin_rastro_de_facu(ENVIOS, "sin listado de campañas")

# ── 4. Cliente con campaña asignada que ya no existe ────────────────────────
print("\n== 4. El cliente tiene asignada una campaña inexistente ==")
limpiar()
for aid in (1, 2, 3):
    cli_de(aid).post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                            "ordenes": [dict(ORDEN)],
                                            "client": "fantasma"})
sin_rastro_de_facu(ENVIOS, "campaña asignada inexistente")

# ── 5. Post sin cliente y sin campaña elegida ───────────────────────────────
print("\n== 5. Post sin cliente y sin campaña elegida ==")
limpiar()
for aid in CUENTAS:
    cli_de(aid).post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                            "ordenes": [dict(ORDEN)]})
sin_rastro_de_facu(ENVIOS, "sin cliente ni campaña")

# ── 6. Intento de forzar los ids de facu desde el navegador ────────────────
print("\n== 6. El front manda a mano la campaña de facu ==")
limpiar()
for aid in (1, 2, 3):
    cli_de(aid).post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                            "ordenes": [dict(ORDEN)],
                                            "client": "peter", "idventa": FACU_VENTA})
sin_rastro_de_facu(ENVIOS, "idventa de facu forzada por el front")

# ── 7. La cola, con órdenes de todas las cuentas ───────────────────────────
print("\n== 7. Reintentos de la cola ==")
limpiar()
for aid in CUENTAS:
    try:
        web._enviar_de_cola({
            "id": aid, "account_id": aid, "user_id": 1, "post_url": "u",
            "client_ig_username": "peter",
            "payload": {"comentarios": COMS,
                        "ordenes": [web._ordenes.normalizar_orden(dict(ORDEN), 0, COMS)],
                        "disponible": None}})
    except Exception as ex:
        print(f"  · cuenta {aid}: no reenvía — {type(ex).__name__}")
sin_rastro_de_facu(ENVIOS, "cola")

# ── 8. Concurrencia entre cuentas mal configuradas ─────────────────────────
print("\n== 8. 30 envíos en paralelo, cuentas mezcladas ==")
limpiar()
errores = []


def go(aid, n):
    try:
        cli_de(aid).post("/api/publicar", json={
            "url": f"u{n}", "comentarios": COMS, "ordenes": [dict(ORDEN)],
            "client": "peter"})
    except Exception as ex:
        errores.append(repr(ex))


hilos = [threading.Thread(target=go, args=(aid, n))
         for n in range(6) for aid in CUENTAS]
for h in hilos:
    h.start()
for h in hilos:
    h.join()
check("ningún hilo explotó", not errores, errores[:3])
sin_rastro_de_facu(ENVIOS, "30 envíos concurrentes")
mezclados = [e for e in ENVIOS if e["email_sesion"] != CUENTAS[e["account_id"]]["email"]]
check("ninguna sesión cruzada entre cuentas", not mezclados, mezclados[:2])

# ── 9. La cuenta de facu SÍ puede seguir usando lo suyo ────────────────────
print("\n== 9. Que no rompimos a facu ==")
limpiar()
CUENTAS[6] = {"email": "facu@growi.com", "idvendedor": FACU_VENDEDOR,
              "idventa": FACU_VENTA, "ventas": [(FACU_VENTA, FACU_VENDEDOR, "999.00", "bora")],
              "desc": "facu"}
r = cli_de(6).post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                          "ordenes": [dict(ORDEN)], "client": "bora"})
check("facu manda con sus propios ids",
      bool(ENVIOS) and ENVIOS[-1]["payload"]["idvendedor"] == FACU_VENDEDOR
      and ENVIOS[-1]["payload"]["idventa"] == FACU_VENTA,
      ENVIOS[-1]["payload"] if ENVIOS else "no mandó")
check("y por su propia sesión (no por la del .env)",
      bool(ENVIOS) and ENVIOS[-1]["email_sesion"] == "facu@growi.com",
      ENVIOS[-1]["email_sesion"] if ENVIOS else "-")
del CUENTAS[6]

# ── 10. El admin sin cuenta sigue andando ─────────────────────────────────
print("\n== 10. El admin (sin cuenta elegida) ==")
limpiar()
c = web.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = None
    s["user_id"] = 1
    s["is_admin"] = True
r = c.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                  "ordenes": [dict(ORDEN)], "client": "peter"})
check("el admin sigue pudiendo mandar", r.status_code == 200 and bool(ENVIOS),
      (r.status_code, len(ENVIOS)))

print("\n" + ("TODO OK — ninguna orden de vendedor cayó en la cuenta de facu"
              if not FALLOS else f"FALLARON {len(FALLOS)}:\n  - " + "\n  - ".join(FALLOS)))
sys.exit(1 if FALLOS else 0)
