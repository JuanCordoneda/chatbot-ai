"""
Testeo MULTI-CUENTA del envío de órdenes, sin tocar el CRM.

El CRM falso se enchufa en `_growi_login_with`, no en `_growi_request`: así se
ejercita de verdad el caché de sesiones por cuenta, el relogin, el parseo de
campañas y `resolver_venta`. Cada sesión falsa queda marcada con el email de la
cuenta que la abrió, así que se puede afirmar POR QUÉ CUENTA salió cada orden.

Lo que se busca romper: que una orden salga con la sesión, el idvendedor o la
campaña de OTRO vendedor.
"""
import json
import sys
import threading

sys.path.insert(0, "/app")

# Nada de red real: si algo se escapa del CRM falso, revienta.
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


# ── Las cuentas ──────────────────────────────────────────────────────────────
# Tres vendedores con credenciales, idvendedor y campañas DISTINTAS.
CUENTAS = {
    1: {"email": "tomas@growi.com",  "idvendedor": "701", "ig": "peter"},
    2: {"email": "lautaro@growi.com", "idvendedor": "702", "ig": "nadia"},
    3: {"email": "facu@growi.com",   "idvendedor": "634", "ig": "bora"},
}
# campañas por cuenta: (idventa, idvendedor, nombre, saldo, ig del perfil)
VENTAS = {
    1: [("8811", "701", "Camp Tomas A", "300.00", "peter"),
        ("8812", "701", "Camp Tomas B", "150.00", "otro")],
    2: [("9911", "702", "Camp Lautaro", "500.00", "nadia")],
    3: [("32600", "634", "Camp Facu", "999.00", "bora")],
}

ENVIOS = []          # cada POST a enviar_trafico que vio el CRM falso
_envios_lock = threading.Lock()
FALLAR_SESION = {"cuenta": None, "veces": 0}   # para simular sesión vencida


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


def _html_campanas(account_id, antiguas):
    filas = []
    for (idv, idvend, nombre, disp, ig) in VENTAS[account_id]:
        filas.append(
            f'<button class="btn seleccionar-cliente" data-id="{idv}" '
            f'data-idvendedor="{idvend}" data-nombre="{nombre}" '
            f'data-correo="c@x.com" data-vendedor="v" data-estadoventa="1" '
            f'data-cantidad-disponible="{disp}" data-monto="1000" '
            f'data-fecha="2026-08-01">Elegir</button>'
        )
    return "<div>" + "".join(filas) + "</div>"


class FakeSession:
    """Sesión CRM falsa. Sabe de QUÉ cuenta es: es lo que permite detectar que
    una orden salió por la sesión equivocada."""

    def __init__(self, account_id, email):
        self.account_id = account_id
        self.email = email
        self.headers = {}
        self.proxies = {}

    def request(self, method, url, timeout=None, **kw):
        if "server_time_ar" in url:
            return FakeResp({"ymdhmAR": "2026-08-09 21:40"})
        if "traer_campanas" in url:
            antiguas = (kw.get("data") or {}).get("antiguas")
            return FakeResp(texto=_html_campanas(self.account_id, antiguas))
        if "editarv.php" in url:
            idv = url.split("idv=")[-1]
            ig = next((v[4] for v in VENTAS[self.account_id] if v[0] == idv), "")
            return FakeResp({"cliente_url": f"https://instagram.com/{ig}/"})
        if "enviar_trafico" in url:
            # Simulación de sesión vencida: el CRM redirige al login (200 + url
            # de login), que es como se comporta Growi de verdad.
            if FALLAR_SESION["cuenta"] == self.account_id and FALLAR_SESION["veces"] > 0:
                FALLAR_SESION["veces"] -= 1
                return FakeResp({}, status=200, url="https://crm.fake/cuenta/login.php")
            with _envios_lock:
                ENVIOS.append({
                    "account_id": self.account_id,
                    "email_sesion": self.email,
                    "payload": kw.get("json"),
                })
            return FakeResp({"success": True, "insertadas": len(kw["json"]["ordenes"]),
                             "messages": ["ok"], "warnings": [], "errors": []})
        raise AssertionError(f"llamada inesperada al CRM: {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


LOGINS = []


def fake_login_with(cfg, verify=True, account_id=None):
    email = cfg.get("crm_email") or "(env)"
    aid = next((a for a, c in CUENTAS.items() if c["email"] == email), None)
    LOGINS.append(email)
    if aid is None:
        # Cuenta sin credenciales propias: cae al .env global. Se marca para
        # poder afirmar que NINGUNA orden de comentarios salió por ahí.
        return FakeSession(0, f"ENV::{email}")
    return FakeSession(aid, email)


web._growi_login_with = fake_login_with
web._log_uso = lambda *a, **kw: None


class FakeRepo:
    """Sólo lo que toca este camino."""

    @staticmethod
    def get_account_crm_config(account_id):
        c = CUENTAS.get(account_id)
        if not c:
            return None
        return {"crm_email": c["email"], "crm_password": "x",
                "crm_url": "https://crm.fake", "crm_proxy": "",
                "crm_idvendedor": c["idvendedor"], "crm_idventa": VENTAS[account_id][0][0],
                "crm_disponible": "150"}

    @staticmethod
    def get_client_by_ig_username(ig, account_id):
        return None

    encolar_orden = staticmethod(lambda post_url, payload, **kw: {"id": 1, **kw})


web._repo = FakeRepo


def limpiar_caches():
    web._growi_sessions.clear()
    web._VENTAS_CACHE.clear()
    web._VENTA_IG_CACHE.clear()
    ENVIOS.clear()
    LOGINS.clear()


def cliente_de(account_id):
    c = web.app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["account_id"] = account_id
        s["user_id"] = 100 + account_id
        s["username"] = CUENTAS[account_id]["email"].split("@")[0]
    return c


COMS = ["mujeres:", "que linda", "diosa", "hombres:", "crack", "grande"]


def orden_coms(nombre="Comentarios verificados", coms=None, cant=4):
    return {"tipo": "comentarios", "redsocialId": "1", "redsocial": "Instagram",
            "productoNombre": nombre, "link": "https://instagram.com/p/abc",
            "costo": 4.5, "cantidad": cant, "cuando": "ahora",
            "comentarios": coms if coms is not None else list(COMS)}


# ── 1. Cada cuenta manda por SU sesión y con SU idvendedor ───────────────────
print("\n== 1. Tres cuentas mandando comentarios, una tras otra ==")
limpiar_caches()
for aid in (1, 2, 3):
    c = cliente_de(aid)
    r = c.post("/api/publicar", json={"url": "https://instagram.com/p/abc",
                                      "comentarios": COMS, "ordenes": [orden_coms()],
                                      "client": CUENTAS[aid]["ig"]})
    check(f"cuenta {aid}: responde 200", r.status_code == 200, r.get_json())

check("salieron 3 envíos", len(ENVIOS) == 3, len(ENVIOS))
for i, aid in enumerate((1, 2, 3)):
    e = ENVIOS[i]
    esperado_vend = CUENTAS[aid]["idvendedor"]
    esperada_venta = VENTAS[aid][0][0]
    check(f"cuenta {aid}: usa SU sesión CRM ({CUENTAS[aid]['email']})",
          e["email_sesion"] == CUENTAS[aid]["email"], e["email_sesion"])
    check(f"cuenta {aid}: idvendedor {esperado_vend}",
          e["payload"]["idvendedor"] == esperado_vend, e["payload"]["idvendedor"])
    check(f"cuenta {aid}: creador {esperado_vend}",
          e["payload"]["creador"] == esperado_vend, e["payload"]["creador"])
    check(f"cuenta {aid}: campaña {esperada_venta} (la del perfil del cliente)",
          e["payload"]["idventa"] == esperada_venta, e["payload"]["idventa"])

check("ninguna orden salió por las credenciales del .env",
      all(not e["email_sesion"].startswith("ENV::") for e in ENVIOS),
      [e["email_sesion"] for e in ENVIOS])
check("ningún envío lleva el idvendedor de otra cuenta",
      len({e["payload"]["idvendedor"] for e in ENVIOS}) == 3,
      [e["payload"]["idvendedor"] for e in ENVIOS])

# ── 2. Concurrencia: las tres a la vez ───────────────────────────────────────
print("\n== 2. Las tres cuentas mandando EN PARALELO (20 envíos) ==")
limpiar_caches()
errores_hilo = []


def enviar(aid, n):
    try:
        c = cliente_de(aid)
        r = c.post("/api/publicar", json={
            "url": f"https://instagram.com/p/{aid}-{n}", "comentarios": COMS,
            "ordenes": [orden_coms(cant=n + 1)], "client": CUENTAS[aid]["ig"]})
        if r.status_code != 200:
            errores_hilo.append((aid, n, r.status_code, r.get_json()))
    except Exception as ex:
        errores_hilo.append((aid, n, repr(ex)))


hilos = [threading.Thread(target=enviar, args=(aid, n))
         for n in range(7) for aid in (1, 2, 3)]
hilos = hilos[:20]
for h in hilos:
    h.start()
for h in hilos:
    h.join()

check("ningún hilo falló", not errores_hilo, errores_hilo)
check(f"llegaron los {len(hilos)} envíos", len(ENVIOS) == len(hilos), len(ENVIOS))
cruzados = [e for e in ENVIOS
            if e["email_sesion"] != CUENTAS[e["account_id"]]["email"]
            or e["payload"]["idvendedor"] != CUENTAS[e["account_id"]]["idvendedor"]]
check("NINGÚN envío salió con la sesión o el idvendedor de otra cuenta",
      not cruzados, cruzados[:3])
por_cuenta = {}
for e in ENVIOS:
    por_cuenta.setdefault(e["account_id"], set()).add(e["payload"]["idventa"])
check("cada cuenta usó sólo campañas propias",
      all(v <= {x[0] for x in VENTAS[a]} for a, v in por_cuenta.items()), por_cuenta)

# ── 3. Cross-tenant: elegir a mano la campaña de OTRO vendedor ───────────────
print("\n== 3. La cuenta 1 intenta cobrarle a la campaña de la cuenta 2 ==")
limpiar_caches()
c = cliente_de(1)
r = c.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                  "ordenes": [orden_coms()], "client": "peter",
                                  "idventa": "9911"})   # ← campaña de Lautaro
e = ENVIOS[-1]
check("la campaña ajena NO se usa", e["payload"]["idventa"] != "9911", e["payload"]["idventa"])
check("cae en una campaña propia", e["payload"]["idventa"] in {v[0] for v in VENTAS[1]},
      e["payload"]["idventa"])
check("sigue saliendo por la sesión de la cuenta 1",
      e["email_sesion"] == CUENTAS[1]["email"], e["email_sesion"])

print("\n== 3b. Y la campaña propia elegida a mano SÍ se respeta ==")
limpiar_caches()
c = cliente_de(1)
r = c.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                  "ordenes": [orden_coms()], "client": "peter",
                                  "idventa": "8812"})
check("usa la campaña 8812 elegida", ENVIOS[-1]["payload"]["idventa"] == "8812",
      ENVIOS[-1]["payload"]["idventa"])

# ── 4. Sesión vencida a mitad de camino ─────────────────────────────────────
print("\n== 4. Al mandar, la sesión de la cuenta 2 está vencida (redirect a login) ==")
limpiar_caches()
FALLAR_SESION.update({"cuenta": 2, "veces": 2})   # dos rebotes y después entra
c = cliente_de(2)
r = c.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                  "ordenes": [orden_coms()], "client": "nadia"})
FALLAR_SESION.update({"cuenta": None, "veces": 0})
check("igual termina entrando", r.status_code == 200 and len(ENVIOS) == 1, (r.status_code, len(ENVIOS)))
check("relogueó (más de un login para esa cuenta)",
      LOGINS.count(CUENTAS[2]["email"]) >= 2, LOGINS)
check("nunca relogueó como otra cuenta",
      all(l == CUENTAS[2]["email"] for l in LOGINS), LOGINS)
check("la orden salió con el idvendedor correcto",
      ENVIOS[-1]["payload"]["idvendedor"] == "702", ENVIOS[-1]["payload"])

# ── 5. La cola: el reintento sale por la cuenta que la cargó ────────────────
print("\n== 5. Cola de reintentos, órdenes de cuentas distintas ==")
limpiar_caches()
for aid in (1, 2, 3):
    crm = web._enviar_de_cola({
        "id": aid, "account_id": aid, "user_id": 100 + aid,
        "post_url": "https://instagram.com/p/abc",
        "client_ig_username": CUENTAS[aid]["ig"],
        "payload": {"comentarios": COMS,
                    "ordenes": [web._ordenes.normalizar_orden(orden_coms(), 0, COMS)],
                    "disponible": None},
    })
    check(f"cola cuenta {aid}: el CRM la acepta", crm.get("success") is True, crm)

for i, aid in enumerate((1, 2, 3)):
    e = ENVIOS[i]
    check(f"cola cuenta {aid}: sale por SU sesión",
          e["email_sesion"] == CUENTAS[aid]["email"], e["email_sesion"])
    check(f"cola cuenta {aid}: con SU idvendedor",
          e["payload"]["idvendedor"] == CUENTAS[aid]["idvendedor"], e["payload"]["idvendedor"])

# ── 6. Cola con una fila SIN account_id (encolada por el código viejo) ──────
print("\n== 6. Fila vieja en la cola, sin account_id ==")
limpiar_caches()
salio = None
try:
    web._enviar_de_cola({
        "id": 99, "account_id": None, "user_id": None,
        "post_url": "u", "client_ig_username": "",
        "payload": {"comentarios": COMS,
                    "ordenes": [web._ordenes.normalizar_orden(orden_coms(), 0, COMS)],
                    "disponible": None},
    })
    salio = ENVIOS[-1] if ENVIOS else None
except Exception as ex:
    salio = f"excepción: {ex!r}"

check("NO se reenvía con las credenciales globales del .env",
      not (isinstance(salio, dict) and salio["email_sesion"].startswith("ENV::")),
      salio)
check("no se manda nada", not ENVIOS, ENVIOS)
check("queda para revisión manual (excepción no reintentable)",
      isinstance(salio, str) and "CuentaSinCRM" in salio, salio)

# El worker tiene que mandarla a 'revisar', no reprogramarla para siempre.
import orden_cola                                                    # noqa: E402
estados = {}
orden_cola._repo = lambda: type("R", (), {
    "tomar_orden_para_reintentar": staticmethod(lambda: {
        "id": 99, "account_id": None, "intentos": 1, "post_url": "u",
        "payload": {"comentarios": COMS, "ordenes": []}}),
    "reprogramar_orden": staticmethod(
        lambda oid, err, reintentable=True: estados.update({"id": oid, "err": err,
                                                            "reintentable": reintentable})),
    "marcar_orden_enviada": staticmethod(lambda oid: estados.update({"enviada": oid})),
})()
orden_cola.procesar_una(web._enviar_de_cola)
check("el worker la manda a revisión (no la reintenta en loop)",
      estados.get("reintentable") is False and "enviada" not in estados, estados)

# ── 7. Cuenta sin CRM propio configurado ────────────────────────────────────
print("\n== 7. Cuenta sin credenciales propias en la DB ==")
limpiar_caches()
c = web.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = 42          # no existe en FakeRepo → cae al .env
    s["user_id"] = 1
r = c.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                  "ordenes": [orden_coms()], "client": "peter"})
uso_env = bool(ENVIOS) and ENVIOS[-1]["email_sesion"].startswith("ENV::")
check("no manda a ciegas con el CRM del .env", not uso_env,
      f"salió como {ENVIOS[-1]['email_sesion']} / idvendedor "
      f"{ENVIOS[-1]['payload']['idvendedor']}" if uso_env else "")
res = (r.get_json() or {}).get("resultado") or {}
check("le explica al vendedor qué pasa",
      "CRM de Growi configurado" in " ".join(res.get("errors") or []), res.get("errors"))
check("no queda encolada (reintentar no lo arregla)", res.get("encolada") is False, res)

print("\n== 7b. El admin (sin cuenta elegida) SÍ puede seguir mandando ==")
limpiar_caches()
c = web.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = None
    s["user_id"] = 1
    s["is_admin"] = True
r = c.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                  "ordenes": [orden_coms()], "client": "peter"})
check("el admin manda con las credenciales del .env, como siempre",
      r.status_code == 200 and bool(ENVIOS) and ENVIOS[-1]["email_sesion"].startswith("ENV::"),
      (r.status_code, [e["email_sesion"] for e in ENVIOS]))

print("\n== 7c. El tráfico de una cuenta sin CRM tampoco se escapa al .env ==")
limpiar_caches()
c = web.app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
    s["account_id"] = 42
    s["user_id"] = 1
r = c.post("/api/enviar_trafico", json={"ordenes": [dict(
    {"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
     "cant_inicial": "10", "cantidad": "10", "programado": 0,
     "fecha_programada": None, "comentarios": []})], "url": "u"})
check("no manda tráfico con el CRM del .env", not ENVIOS, [e["email_sesion"] for e in ENVIOS])
check("responde error al front", r.status_code == 500 and "error" in (r.get_json() or {}),
      (r.status_code, r.get_json()))

# ── 8. Entradas raras de comentarios ────────────────────────────────────────
print("\n== 8. Listas de comentarios raras ==")
limpiar_caches()
c = cliente_de(1)

r = c.post("/api/publicar", json={"url": "u", "comentarios": ["solo uno"],
                                  "ordenes": [orden_coms(coms=[], cant=1)]})
check("orden sin lista propia usa la lista global",
      r.status_code == 200 and ENVIOS[-1]["payload"]["ordenes"][0]["comentarios"] == ["solo uno"],
      (r.status_code, ENVIOS[-1]["payload"]["ordenes"][0]["comentarios"] if ENVIOS else None))

n = len(ENVIOS)
r = c.post("/api/publicar", json={"url": "u", "comentarios": [], "ordenes": [orden_coms()]})
check("sin comentarios → 400 y no se manda nada",
      r.status_code == 400 and len(ENVIOS) == n, (r.status_code, len(ENVIOS)))

n = len(ENVIOS)
r = c.post("/api/publicar", json={"url": "u", "comentarios": COMS, "ordenes": []})
check("sin órdenes → 400 y no se manda nada",
      r.status_code == 400 and len(ENVIOS) == n, (r.status_code, len(ENVIOS)))

r = c.post("/api/publicar", json={"url": "u", "comentarios": ["mujeres:", "hombres:"],
                                  "ordenes": [orden_coms(coms=["mujeres:", "hombres:"], cant=0)]})
check("sólo encabezados: no rompe y los conserva",
      r.status_code == 200 and ENVIOS[-1]["payload"]["ordenes"][0]["comentarios"] == ["mujeres:", "hombres:"],
      (r.status_code, ENVIOS[-1]["payload"]["ordenes"][0]["comentarios"]))

largos = [f"comentario numero {i}" for i in range(200)]
r = c.post("/api/publicar", json={"url": "u", "comentarios": largos,
                                  "ordenes": [orden_coms(coms=largos, cant=200)]})
check("200 comentarios llegan completos y sin duplicar",
      sorted(ENVIOS[-1]["payload"]["ordenes"][0]["comentarios"]) == sorted(largos),
      len(ENVIOS[-1]["payload"]["ordenes"][0]["comentarios"]))

# ── 9. Que el tráfico de una cuenta tampoco se cruce ────────────────────────
print("\n== 9. Tráfico y comentarios de cuentas distintas, intercalados ==")
limpiar_caches()
CRM_ORDEN = {"redsocial_id": "1", "redsocial": "Instagram", "prod": "Followers",
             "demora": " - ", "url": "https://instagram.com/p/abc", "costo": 12.0,
             "obs": "", "cant_inicial": "500", "cantidad": "500",
             "programado": 0, "fecha_programada": None, "comentarios": []}
cliente_de(1).post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)],
                                                "client": "peter", "url": "u"})
cliente_de(2).post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                          "ordenes": [orden_coms()], "client": "nadia"})
cliente_de(3).post("/api/enviar_trafico", json={"ordenes": [dict(CRM_ORDEN)],
                                                "client": "bora", "url": "u"})
check("3 envíos", len(ENVIOS) == 3, len(ENVIOS))
check("tráfico cuenta 1 con idvendedor 701", ENVIOS[0]["payload"]["idvendedor"] == "701",
      ENVIOS[0]["payload"]["idvendedor"])
check("comentarios cuenta 2 con idvendedor 702", ENVIOS[1]["payload"]["idvendedor"] == "702",
      ENVIOS[1]["payload"]["idvendedor"])
check("tráfico cuenta 3 con idvendedor 634", ENVIOS[2]["payload"]["idvendedor"] == "634",
      ENVIOS[2]["payload"]["idvendedor"])
check("cada uno por su propia sesión",
      [e["email_sesion"] for e in ENVIOS] == [CUENTAS[1]["email"], CUENTAS[2]["email"],
                                              CUENTAS[3]["email"]],
      [e["email_sesion"] for e in ENVIOS])

# ── 10. El caché de campañas no se comparte entre cuentas ───────────────────
print("\n== 10. Caché de campañas por cuenta ==")
limpiar_caches()
v1 = web._traer_ventas(1)
v2 = web._traer_ventas(2)
check("cuenta 1 ve sólo sus campañas",
      {v["idventa"] for v in v1} == {x[0] for x in VENTAS[1]}, [v["idventa"] for v in v1])
check("cuenta 2 ve sólo la suya",
      {v["idventa"] for v in v2} == {x[0] for x in VENTAS[2]}, [v["idventa"] for v in v2])
check("el caché está separado por cuenta",
      set(web._VENTAS_CACHE.keys()) == {1, 2}, list(web._VENTAS_CACHE.keys()))

print("\n" + ("TODO OK" if not FALLOS else f"FALLARON {len(FALLOS)}:\n  - " + "\n  - ".join(FALLOS)))
sys.exit(1 if FALLOS else 0)
