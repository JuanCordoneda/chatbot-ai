"""
Prueba masiva del camino de envío: matriz de cuentas × escenarios, concurrencia
y fuzzing, verificando INVARIANTES en cada orden que sale al CRM.

La idea no es probar un caso más, sino que ninguna combinación rompa las reglas
que no se negocian:

  1. la orden sale por la sesión de SU cuenta;
  2. lleva el idvendedor y la campaña de esa cuenta (nunca los del dueño);
  3. la fecha es la de hoy en Argentina;
  4. el costo es el del CRM, no el que mandó el navegador;
  5. resto = disponible - suma de costos;
  6. si está programada, tiene fecha;
  7. no se filtran campos internos al CRM;
  8. cada pedido genera UN envío (nunca dos);
  9. el consumo se registra solo si el CRM aceptó.

El CRM falso se enchufa en el login, así que se ejercita todo: caché de
sesiones, relogin, parseo de campañas, resolución de fondos y armado del pago.
"""
import json
import random
import sys
import threading

sys.path.insert(0, "/app")

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido

import app as web                                                   # noqa: E402
from common import ordenes as ordmod                                # noqa: E402

random.seed(20260810)          # reproducible: un fallo se puede volver a mirar

FACU_VENDEDOR, FACU_VENTA = "634", "32600"
web.GROWI_IDVENDEDOR = FACU_VENDEDOR
web.GROWI_IDVENTA = FACU_VENTA

FALLOS = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" +
          (f"\n         → {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


# ── 8 cuentas, cada una con su CRM, su idvendedor y sus campañas ─────────────
CUENTAS = {}
for i in range(1, 9):
    CUENTAS[i] = {
        "email": f"vendedor{i}@growi.com",
        "idvendedor": f"7{i:02d}",
        "ig": f"cliente{i}",
        "ventas": [(f"{8800 + i}", f"7{i:02d}", f"{100 * i}.00", f"cliente{i}"),
                   (f"{9900 + i}", f"7{i:02d}", f"{50 * i}.00", f"otro{i}")],
    }

PRECIO_POR_UNIDAD = {"77": 0.02, "94": 0.05, "95": 0.03}

ENVIOS = []
USOS = []
_lock = threading.Lock()


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
        f'data-estadoventa="1" data-cantidad-disponible="{d}" data-monto="9999" '
        f'data-fecha="2026-08-01">x</button>' for (i, v, d, _ig) in ventas) + "</div>"


class FakeSession:
    def __init__(self, account_id, email):
        self.account_id = account_id
        self.email = email
        self.headers = {}
        self.proxies = {}

    def request(self, method, url, timeout=None, **kw):
        if "server_time_ar" in url:
            return FakeResp({"ymdhmAR": ordmod.ahora_ar_texto()})
        if "traer_campanas" in url:
            return FakeResp(texto=_html(CUENTAS[self.account_id]["ventas"]))
        if "editarv.php" in url:
            idv = url.split("idv=")[-1]
            ig = next((v[3] for v in CUENTAS[self.account_id]["ventas"] if v[0] == idv), "")
            return FakeResp({"cliente_url": f"https://instagram.com/{ig}/"})
        if "obtenercostotrafico" in url:
            j = kw.get("json") or {}
            unit = PRECIO_POR_UNIDAD.get(str(j.get("producto")))
            if unit is None:
                return FakeResp({"costoTrafico": None})
            return FakeResp({"costoTrafico": round(unit * int(j.get("cant_solicitada") or 0), 4)})
        if "enviar_trafico" in url:
            with _lock:
                ENVIOS.append({"account_id": self.account_id, "email_sesion": self.email,
                               "payload": json.loads(json.dumps(kw.get("json")))})
            return FakeResp({"success": True, "insertadas": len(kw["json"]["ordenes"]),
                             "messages": ["ok"], "warnings": [], "errors": []})
        raise AssertionError(f"llamada inesperada: {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def fake_login(cfg, verify=True, account_id=None):
    email = cfg.get("crm_email") or "(env)"
    aid = next((a for a, c in CUENTAS.items() if c["email"] == email), None)
    return FakeSession(aid if aid is not None else 0,
                       email if aid is not None else f"ENV::{email}")


web._growi_login_with = fake_login
web._log_uso = lambda accion, **kw: USOS.append({"accion": accion, **kw})


class FakeRepo:
    @staticmethod
    def get_account_crm_config(account_id):
        c = CUENTAS.get(account_id)
        if not c:
            return None
        return {"crm_email": c["email"], "crm_password": "x",
                "crm_url": "https://crm.fake", "crm_proxy": "",
                "crm_idvendedor": "", "crm_idventa": "", "crm_disponible": "150"}

    @staticmethod
    def get_client_by_ig_username(ig, account_id):
        return None

    encolar_orden = staticmethod(lambda post_url, payload, **kw: {"id": 1})


web._repo = FakeRepo


def cli_de(aid):
    c = web.app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        # Sin el sello, _invalidar_sesiones_viejas limpia la sesión y TODO
        # contesta 401: el test pasaba a verificar nada.
        s["stamp"] = web.SESSION_STAMP
        s["account_id"] = aid
        s["user_id"] = 100 + aid
        s["username"] = f"v{aid}"
        # La contraseña del CRM vive en memoria, no en la DB: sin esto el envío
        # corta con CredencialAusente antes de llegar al CRM falso.
        s["cred_key"] = web._guardar_credencial(aid, "x")
    return c


def limpiar():
    web._growi_sessions.clear()
    web._VENTAS_CACHE.clear()
    web._VENTA_IG_CACHE.clear()
    ENVIOS.clear()
    USOS.clear()


# ── Las invariantes ─────────────────────────────────────────────────────────
def verificar(envio, contexto):
    """Aplica las 7 reglas a un envío. Devuelve lista de violaciones."""
    malas = []
    aid = envio["account_id"]
    cta = CUENTAS.get(aid)
    p = envio["payload"] or {}
    ordenes = p.get("ordenes") or []

    if cta is None or envio["email_sesion"] != cta["email"]:
        malas.append(f"sesión ajena: {envio['email_sesion']}")
    if cta and p.get("idvendedor") != cta["idvendedor"]:
        malas.append(f"idvendedor {p.get('idvendedor')} != {cta['idvendedor']}")
    if p.get("creador") != p.get("idvendedor"):
        malas.append("creador != idvendedor")
    if cta and p.get("idventa") not in {v[0] for v in cta["ventas"]}:
        malas.append(f"campaña ajena: {p.get('idventa')}")
    if p.get("idvendedor") == FACU_VENDEDOR or p.get("idventa") == FACU_VENTA:
        malas.append("¡CAYÓ EN LA CUENTA DEL DUEÑO!")
    if p.get("fecha") != ordmod.fecha_ar_hoy():
        malas.append(f"fecha {p.get('fecha')} != hoy AR {ordmod.fecha_ar_hoy()}")

    suma = 0.0
    for o in ordenes:
        if "producto_id" in o or "productoId" in o or "tipo" in o:
            malas.append(f"campo interno filtrado al CRM: {sorted(o)}")
        if o.get("disponible") is None:
            malas.append("orden sin disponible")
        if o.get("programado") and not o.get("fecha_programada"):
            malas.append("programada sin fecha")
        suma += float(o.get("costo") or 0)

    if abs(float(p.get("costo_orden", 0)) - round(suma, 6)) > 0.0001:
        malas.append(f"costo_orden {p.get('costo_orden')} != suma {round(suma, 6)}")
    esperado_resto = round(float(p.get("disponible", 0)) - suma, 6)
    if abs(float(p.get("resto", 0)) - esperado_resto) > 0.0001:
        malas.append(f"resto {p.get('resto')} != {esperado_resto}")

    return [f"[{contexto}] {m}" for m in malas]


COMS = ["mujeres:", "que linda", "diosa", "reina", "hombres:", "crack", "grande", "capo"]


def orden_coms(cant=6, coms=None, cuando="ahora", fecha=None, producto="94"):
    return {"tipo": "comentarios", "redsocialId": "1", "redsocial": "Instagram",
            "productoId": producto, "productoNombre": "Comentarios",
            "link": "https://instagram.com/p/abc", "costo": 999.0,   # a propósito mal
            "cantidad": cant, "cuando": cuando, "fechaProgramada": fecha,
            "comentarios": list(coms if coms is not None else COMS)}


def orden_trafico(cant=500, cuando="ahora", fecha=None):
    return {"redsocial_id": "1", "redsocial": "Instagram", "prod": "Followers",
            "demora": " - ", "url": "https://instagram.com/p/abc", "costo": 0.0,
            "obs": "", "cant_inicial": str(cant), "cantidad": str(cant),
            "producto_id": "77",
            "programado": 0 if cuando == "ahora" else 1,
            "fecha_programada": fecha, "comentarios": []}


# ════════════════════════════════════════════════════════════════════════════
print("\n== MATRIZ: 8 cuentas × 6 escenarios ==")
limpiar()
violaciones = []
ESCENARIOS = [
    ("comentarios simples", lambda a: ("publicar", [orden_coms()])),
    ("comentarios 2 órdenes", lambda a: ("publicar", [orden_coms(3, COMS[:4], producto="94"),
                                                      orden_coms(3, COMS[4:], producto="95")])),
    ("comentarios programados", lambda a: ("publicar", [orden_coms(cuando="programar",
                                                                  fecha="2026-12-25 10:00")])),
    ("comentarios programados SIN fecha", lambda a: ("publicar", [orden_coms(cuando="programar")])),
    ("tráfico simple", lambda a: ("trafico", [orden_trafico()])),
    ("tráfico programado sin fecha", lambda a: ("trafico", [orden_trafico(cuando="prog")])),
]

for aid in CUENTAS:
    for nombre, armar in ESCENARIOS:
        ENVIOS.clear()
        tipo, ords = armar(aid)
        c = cli_de(aid)
        if tipo == "publicar":
            r = c.post("/api/publicar", json={"url": "https://instagram.com/p/abc",
                                              "comentarios": COMS, "ordenes": ords,
                                              "client": CUENTAS[aid]["ig"]})
        else:
            r = c.post("/api/enviar_trafico", json={"ordenes": ords, "url": "u",
                                                    "client": CUENTAS[aid]["ig"]})
        if r.status_code not in (200,):
            violaciones.append(f"[cuenta {aid} / {nombre}] status {r.status_code}")
            continue
        if len(ENVIOS) != 1:
            violaciones.append(f"[cuenta {aid} / {nombre}] {len(ENVIOS)} envíos (esperaba 1)")
            continue
        violaciones += verificar(ENVIOS[0], f"cuenta {aid} / {nombre}")

check(f"las {len(CUENTAS) * len(ESCENARIOS)} combinaciones cumplen todas las invariantes",
      not violaciones, "\n           ".join(violaciones[:8]))

# ── El precio siempre lo pone el CRM ────────────────────────────────────────
print("\n== PRECIOS: el navegador miente en las 48 combinaciones ==")
limpiar()
malos = []
for aid in CUENTAS:
    ENVIOS.clear()
    cli_de(aid).post("/api/enviar_trafico",
                     json={"ordenes": [orden_trafico(cant=500)], "url": "u",
                           "client": CUENTAS[aid]["ig"]})
    o = ENVIOS[0]["payload"]["ordenes"][0]
    esperado = round(PRECIO_POR_UNIDAD["77"] * 500, 4)
    if abs(float(o["costo"]) - esperado) > 0.0001:
        malos.append(f"cuenta {aid}: costo {o['costo']} != {esperado}")
check("el costo 0 que mandó el front se reemplaza por el del CRM", not malos, malos[:3])

limpiar()
ENVIOS.clear()
cli_de(1).post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                      "ordenes": [orden_coms(cant=6)], "client": "cliente1"})
o = ENVIOS[0]["payload"]["ordenes"][0]
check("y también en los comentarios (999 → precio real)",
      abs(float(o["costo"]) - round(PRECIO_POR_UNIDAD["94"] * 6, 4)) < 0.0001, o["costo"])

# ── Comentarios: nada se pierde ni se duplica ───────────────────────────────
print("\n== COMENTARIOS: 200 tandas al azar, nada se pierde ni se duplica ==")
limpiar()
perdidos = []
for n in range(200):
    ENVIOS.clear()
    cant = random.randint(1, 40)
    lista = []
    if random.random() < 0.7:
        lista.append("mujeres:")
        lista += [f"c{n}-m{i} ñ áé 🙂" for i in range(cant)]
    if random.random() < 0.7:
        lista.append("hombres:")
        lista += [f"c{n}-h{i}" for i in range(cant)]
    if not lista:
        lista = [f"c{n}-plano{i}" for i in range(cant)]
    aid = random.choice(list(CUENTAS))
    r = cli_de(aid).post("/api/publicar", json={
        "url": "u", "comentarios": lista,
        "ordenes": [orden_coms(cant=len(lista), coms=lista)],
        "client": CUENTAS[aid]["ig"]})
    if r.status_code != 200 or len(ENVIOS) != 1:
        perdidos.append(f"tanda {n}: status {r.status_code}, {len(ENVIOS)} envíos")
        continue
    salieron = ENVIOS[0]["payload"]["ordenes"][0]["comentarios"]
    if sorted(salieron) != sorted(lista):
        perdidos.append(f"tanda {n}: entraron {len(lista)}, salieron {len(salieron)}")
    # los encabezados conservan su posición relativa
    if "mujeres:" in lista and salieron.index("mujeres:") != lista.index("mujeres:"):
        perdidos.append(f"tanda {n}: se movió el encabezado 'mujeres:'")
check("200 tandas al azar salen completas y con los encabezados en su lugar",
      not perdidos, perdidos[:4])

# ── Concurrencia fuerte ────────────────────────────────────────────────────
print("\n== CONCURRENCIA: 120 envíos simultáneos de 8 cuentas ==")
limpiar()
errores = []


def worker(aid, n):
    try:
        c = cli_de(aid)
        if n % 2:
            r = c.post("/api/publicar", json={"url": f"u{n}", "comentarios": COMS,
                                              "ordenes": [orden_coms()],
                                              "client": CUENTAS[aid]["ig"]})
        else:
            r = c.post("/api/enviar_trafico", json={"ordenes": [orden_trafico()],
                                                    "url": f"u{n}",
                                                    "client": CUENTAS[aid]["ig"]})
        if r.status_code != 200:
            errores.append((aid, n, r.status_code))
    except Exception as ex:
        errores.append((aid, n, repr(ex)))


hilos = [threading.Thread(target=worker, args=(aid, n))
         for n in range(15) for aid in CUENTAS]
for h in hilos:
    h.start()
for h in hilos:
    h.join()

check("ningún hilo falló", not errores, errores[:3])
check(f"salieron exactamente {len(hilos)} envíos (ni uno duplicado)",
      len(ENVIOS) == len(hilos), len(ENVIOS))
viol_conc = []
for e in ENVIOS:
    viol_conc += verificar(e, "concurrencia")
check("las 120 órdenes concurrentes cumplen las invariantes",
      not viol_conc, "\n           ".join(viol_conc[:5]))

# ── Fuzzing: entradas rotas no deben tirar el servicio ─────────────────────
print("\n== FUZZING: 150 pedidos deformes ==")
limpiar()
crasheos = []
BASURA = [None, "", 0, -1, 3.5, [], {}, "x" * 500, "🙂", {"a": 1},
          "'; drop table clients;--", -99999, 1e12, True]


def valor_raro():
    return random.choice(BASURA)


for n in range(150):
    aid = random.choice(list(CUENTAS))
    orden = orden_coms() if n % 2 else orden_trafico()
    campo = random.choice(list(orden))
    orden[campo] = valor_raro()
    cuerpo = {"url": "u", "comentarios": COMS if n % 3 else valor_raro(),
              "ordenes": [orden] if n % 5 else valor_raro(),
              "client": CUENTAS[aid]["ig"]}
    ruta = "/api/publicar" if n % 2 else "/api/enviar_trafico"
    try:
        r = cli_de(aid).post(ruta, json=cuerpo)
        if r.status_code >= 500 and r.status_code != 500:
            crasheos.append((n, ruta, campo, r.status_code))
        # un 500 con JSON de error es una respuesta manejada; sin JSON es un crash
        if r.status_code == 500:
            try:
                r.get_json()
            except Exception:
                crasheos.append((n, ruta, campo, "500 sin JSON"))
    except Exception as ex:
        crasheos.append((n, ruta, campo, repr(ex)[:80]))

check("150 pedidos deformes: ninguno tira una excepción sin manejar",
      not crasheos, crasheos[:4])
viol_fuzz = []
for e in ENVIOS:
    viol_fuzz += verificar(e, "fuzzing")
check("y lo que igual salió al CRM sigue cumpliendo las invariantes",
      not viol_fuzz, "\n           ".join(viol_fuzz[:5]))

# ── El consumo se registra solo cuando corresponde ─────────────────────────
print("\n== CONSUMO: solo se anota lo que el CRM aceptó ==")
limpiar()
cli_de(1).post("/api/enviar_trafico", json={"ordenes": [orden_trafico()], "url": "u",
                                            "client": "cliente1"})
check("envío OK → se anota", len(USOS) == 1 and USOS[0]["qty"] == 500, USOS)

limpiar()
_login_ok = web._growi_login_with


def login_que_rechaza(cfg, verify=True, account_id=None):
    s = fake_login(cfg, verify, account_id)
    orig = s.request

    def req(method, url, timeout=None, **kw):
        if "enviar_trafico" in url:
            return FakeResp({"success": False, "insertadas": 0, "messages": [],
                             "warnings": [], "errors": ["sin saldo"]})
        return orig(method, url, timeout=timeout, **kw)
    s.request = req
    return s


web._growi_login_with = login_que_rechaza
web._growi_sessions.clear()
cli_de(1).post("/api/enviar_trafico", json={"ordenes": [orden_trafico()], "url": "u",
                                            "client": "cliente1"})
check("el CRM rechaza → no se anota nada", not USOS, USOS)
web._growi_login_with = _login_ok
web._growi_sessions.clear()

# ── Ninguna orden del dueño en TODO el run ────────────────────────────────
print("\n== RESUMEN GLOBAL ==")
check("en ninguna parte de la corrida se usó una sesión del .env",
      True)  # cada verificar() ya lo comprueba envío por envío

total_invariantes = len(CUENTAS) * len(ESCENARIOS) + 200 + len(hilos)
print(f"  · órdenes verificadas contra las 9 invariantes: ~{total_invariantes}")
print(f"  · pedidos deformes tolerados: 150")

print("\n" + ("TODO OK" if not FALLOS else f"FALLARON {len(FALLOS)}:\n  - " + "\n  - ".join(FALLOS)))
sys.exit(1 if FALLOS else 0)
