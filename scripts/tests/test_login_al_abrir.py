"""Abrir la página sin sesión del CRM tiene que llevar al login EN EL ACTO.

El agujero que cubre: la contraseña de Growi vive en memoria del proceso y la
cookie de Flask dura 30 días. Al reiniciarse el web-service la cookie sobrevive
pero la contraseña no, así que el vendedor entraba, veía la pantalla completa,
pegaba el link, generaba comentarios... y recién ahí saltaba el 401. Desde el
lado de él eso es "no anda". El chequeo tiene que pasar ANTES de servir la
página, no cuando algo toca el CRM.

    PYTHONPATH=.:webService python3 scripts/tests/test_login_al_abrir.py
"""
import os
import re
import sys
import time
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webService"))

import app as web                                                   # noqa: E402

FALLOS = []


def check(nombre, cond, extra=""):
    print(f"  {'OK   ' if cond else 'FALLA'} {nombre}" + (f"\n         → {extra}" if not cond and extra else ""))
    if not cond:
        FALLOS.append(nombre)


class RepoFake:
    RepoError = RuntimeError

    @staticmethod
    def get_account_crm_config(aid):
        return {"crm_email": "facu@growi.com", "crm_url": "https://crm.test",
                "crm_idvendedor": "77", "crm_idventa": "88"}

    @staticmethod
    def log_usage(**kw):
        return None

    @staticmethod
    def encolar_orden(post_url, payload, **kw):
        ENCOLADAS.append(payload)
        return {"id": 77}


ENCOLADAS = []


web._repo = RepoFake


def cliente(logueado=True, account_id=3, cred=None, admin=False, stamp=None):
    """Un navegador con la cookie ya puesta. `cred`: contraseña en memoria (o
    None para simular el reinicio del servicio)."""
    c = web.app.test_client()
    with c.session_transaction() as s:
        if not logueado:
            return c
        s["logged_in"] = True
        s["stamp"] = web.SESSION_STAMP if stamp is None else stamp
        s["account_id"] = account_id
        s["user_id"] = 9
        s["username"] = "admin" if admin else "facu"
        s["is_admin"] = admin
        if cred is not None:
            s["cred_key"] = web._guardar_credencial(account_id, cred)
    return c


def destino(r):
    return r.headers.get("Location", "")


def es_login(r, con_motivo=True):
    u = urlparse(destino(r))
    if u.path != "/login":
        return False
    return (not con_motivo) or parse_qs(u.query).get("motivo") == ["sesion_crm"]


def next_de(r):
    return (parse_qs(urlparse(destino(r)).query).get("next") or [""])[0]


print("== 1. Home con la cookie viva pero sin contraseña del CRM ==")
cli = cliente(cred=None)
r = cli.get("/")
check("no sirve la página: redirige", r.status_code in (302, 303), r.status_code)
check("y redirige al login con el motivo", es_login(r), destino(r))
check("guardándose a dónde quería ir", next_de(r) == "/", destino(r))
cuerpo = r.get_data(as_text=True)
check("no filtra nada de la app en el cuerpo del redirect",
      "generar" not in cuerpo.lower(), cuerpo[:200])

print("\n== 2. Navegar NO le arranca la cookie al resto del navegador ==")
# Esto costó DOS regresiones seguidas. Primero limpiaba el guard; después limpiaba
# login() un salto más tarde: da igual, el Set-Cookie de borrado vale para TODO el
# navegador. Las otras pestañas quedaban sin sesión, sus 401 pasaban a ser
# "No autenticado" pelados —sin la marca `relogin`— y el interceptor de app.js no
# las llevaba a ningún lado.
#
# Se sigue el redirect A PROPÓSITO (follow_redirects=True): mirar la sesión en el
# 302, sin seguirlo, mide un estado intermedio que ningún navegador vive, y el
# test daba verde con el bug puesto.
cli = cliente(cred=None)
r = cli.get("/", follow_redirects=True)
check("aterriza en el formulario de login", 'name="password"' in r.get_data(as_text=True))
with cli.session_transaction() as s:
    check("y la sesión del navegador sigue en pie para las otras pestañas",
          s.get("logged_in") is True, dict(s))

print("\n== 3. Todas las pantallas, no solo la home ==")
for ruta in ["/", "/followers", "/mis-clientes", "/admin"]:
    r = cliente(cred=None).get(ruta)
    check(f"{ruta} manda al login", r.status_code in (302, 303) and es_login(r),
          f"{r.status_code} {destino(r)}")

print("\n== 4. Con la contraseña en memoria la página se sirve normal ==")
r = cliente(cred="secreta").get("/")
check("la home carga", r.status_code == 200, r.status_code)
check("y sin cachearse (si no, la ve después de deslogueado)",
      "no-store" in r.headers.get("Cache-Control", ""), r.headers.get("Cache-Control"))
r = cliente(cred="secreta").get("/followers")
check("followers también carga", r.status_code == 200, r.status_code)

print("\n== 5. El admin no queda afuera (opera con la config del .env) ==")
r = cliente(account_id=None, admin=True, cred=None).get("/")
check("el admin entra sin contraseña en memoria", r.status_code == 200, r.status_code)

print("\n== 6. Credencial VENCIDA = credencial ausente ==")
cli = cliente(cred="secreta")
with cli.session_transaction() as s:
    clave = s["cred_key"]
web._CREDENCIALES[clave]["ts"] = time.time() - web._cred_ttl() - 10
r = cli.get("/")
check("la vencida no sirve para entrar", r.status_code in (302, 303) and es_login(r),
      f"{r.status_code} {destino(r)}")

print("\n== 7. Credencial de OTRA cuenta no vale ==")
cli = web.app.test_client()
with cli.session_transaction() as s:
    s.update({"logged_in": True, "stamp": web.SESSION_STAMP, "account_id": 3,
              "user_id": 9, "username": "facu", "is_admin": False,
              "cred_key": web._guardar_credencial(999, "secreta")})
r = cli.get("/")
check("no se presta la contraseña entre cuentas",
      r.status_code in (302, 303) and es_login(r), f"{r.status_code} {destino(r)}")

print("\n== 8. cred_key inventada / basura ==")
cli = web.app.test_client()
with cli.session_transaction() as s:
    s.update({"logged_in": True, "stamp": web.SESSION_STAMP, "account_id": 3,
              "user_id": 9, "username": "facu", "cred_key": "no-existe-esta-clave"})
r = cli.get("/")
check("una clave que no está en memoria manda al login",
      r.status_code in (302, 303) and es_login(r), f"{r.status_code} {destino(r)}")

print("\n== 9. Sin cookie / sesión de un deploy viejo ==")
r = cliente(logueado=False).get("/")
check("sin sesión sigue yendo al login", r.status_code in (302, 303)
      and es_login(r, con_motivo=False), f"{r.status_code} {destino(r)}")
r = cliente(cred="secreta", stamp="deploy-anterior").get("/")
check("sesión de otro deploy: al login", r.status_code in (302, 303)
      and es_login(r, con_motivo=False), f"{r.status_code} {destino(r)}")

print("\n== 10. Nada de bucles de redirección ==")
# Se siguen los saltos DE VERDAD y se cuenta: el check anterior ("destino != ruta")
# era una tautología que sobrevivía a sacar /logout de la lista de exentos.
for ruta in ["/", "/followers", "/login", "/logout", "/admin"]:
    r = cliente(cred=None).get(ruta, follow_redirects=True)
    saltos = len(r.history)
    check(f"{ruta} termina en una página servida (200) en ≤2 saltos",
          r.status_code == 200 and saltos <= 2, f"{r.status_code}, {saltos} saltos")
    check(f"{ruta} termina en el formulario de login",
          'name="password"' in r.get_data(as_text=True), r.request.path)
r = cliente(cred=None).get("/login")
check("el login se sirve entero (200)", r.status_code == 200, r.status_code)
check("y explica por qué lo mandaron ahí cuando trae el motivo",
      "venció" in cliente(cred=None).get("/login?motivo=sesion_crm").get_data(as_text=True).lower())

print("\n== 11. Las guías públicas siguen abiertas ==")
for ruta in ["/ayuda", "/ayuda-ordenes"]:
    r = cliente(cred=None).get(ruta)
    check(f"{ruta} sigue sirviéndose", r.status_code == 200, r.status_code)
    r = cliente(logueado=False).get(ruta)
    check(f"{ruta} sin sesión también", r.status_code == 200, r.status_code)

print("\n== 12. El guard NO toca las /api/: siguen exactamente como antes ==")
# Cortarlas acá rompía dos cosas reales (por eso está explícito en el código):
#   a) /api/stream/<job_id> es el ÚNICO endpoint sin @require_login, justo para
#      que la reconexión sobreviva a un reinicio. Atajarlo tiraba la tanda ya
#      generada y los tokens ya pagados.
#   b) _respuesta_relogin() limpia la sesión, así que un GET cualquiera dejaba
#      sin cookie al POST /api/publicar siguiente: moría en require_login antes
#      de llegar al except que guarda la orden.
for ruta in ["/api/me", "/api/mi-whatsapp", "/api/wa-estado", "/api/stream/1"]:
    c = cliente(cred=None)
    r = c.get(ruta)
    check(f"{ruta} sigue contestando como siempre (200)", r.status_code == 200,
          r.status_code)
    with c.session_transaction() as s:
        check(f"{ruta} no le arranca la sesión", s.get("logged_in") is True, dict(s))

print("\n== 12 bis. Dos pestañas en el MISMO navegador: la que no navegó no queda colgada ==")
# El test_client tiene un solo tarro de cookies, igual que un navegador con dos
# pestañas. La pestaña A navega y la mandan al login (siguiendo el redirect, como
# haría el navegador de verdad); la pestaña B sigue abierta con la tanda generada.
ENCOLADAS.clear()
c = cliente(cred=None)
c.get("/", follow_redirects=True)          # pestaña A: navega y aterriza en el login
r = c.get("/api/me")                       # pestaña B: sigue viva
check("la pestaña B conserva la sesión", r.status_code == 200, r.status_code)
r = c.get("/api/productos?rrss=1")         # pestaña B toca el CRM
d = r.get_json(silent=True) or {}
check("su 401 trae la marca `relogin` que la lleva al login",
      r.status_code == 401 and d.get("relogin") is True, f"{r.status_code} {d}")
# Y lo que más importa: que la pestaña B pueda salvar la orden que tenía armada.
c = cliente(cred=None)
c.get("/", follow_redirects=True)
r = c.post("/api/publicar", json={
    "url": "https://ig.com/p/x", "client": "vip", "comentarios": ["a", "b"],
    "ordenes": [{"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
                 "cant_inicial": "10", "cantidad": "10", "programado": 0,
                 "fecha_programada": None, "comentarios": [], "producto_id": "77"}]})
d = r.get_json(silent=True) or {}
check("y su orden se sigue guardando en 'Órdenes que no entraron'",
      d.get("encolada") is True and ENCOLADAS, f"{r.status_code} {d}")

print("\n== 12 ter. Un POST con trabajo adentro NO se corta antes de guardarlo ==")
# Si el chequeo preventivo atajara también los POST, publicar sin credencial
# mandaría al login TIRANDO la orden que el vendedor acaba de armar.
ENCOLADAS.clear()
r = cliente(cred=None).post("/api/publicar", json={
    "url": "https://ig.com/p/x", "client": "vip", "comentarios": ["a", "b"],
    "ordenes": [{"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
                 "cant_inicial": "10", "cantidad": "10", "programado": 0,
                 "fecha_programada": None, "comentarios": [], "producto_id": "77"}]})
d = r.get_json(silent=True) or {}
check("igual manda al login", r.status_code == 401 and d.get("relogin") is True, d)
check("pero la orden quedó guardada primero", d.get("encolada") is True and ENCOLADAS, d)

print("\n== 12 quater. La pestaña que quedó abierta se entera al volver al foco ==")
# El guard solo corre cuando la pantalla NAVEGA. El que deja el panel abierto toda
# la jornada nunca navega: se enteraba recién en el paso de órdenes, con la tanda
# ya generada y los tokens ya gastados.
r = cliente(cred=None).get("/api/sesion-viva")
d = r.get_json(silent=True) or {}
check("sin contraseña del CRM avisa que hay que reloguear",
      r.status_code == 401 and d.get("relogin") is True, f"{r.status_code} {d}")
c = cliente(cred=None)
c.get("/api/sesion-viva")
with c.session_transaction() as s:
    check("y el chequeo NO le arranca la sesión de paso", s.get("logged_in") is True, dict(s))
r = cliente(cred="secreta").get("/api/sesion-viva")
check("con la contraseña puesta no molesta a nadie",
      r.status_code == 200 and (r.get_json(silent=True) or {}).get("ok") is True,
      f"{r.status_code} {r.get_json(silent=True)}")
r = cliente(account_id=None, admin=True, cred=None).get("/api/sesion-viva")
check("y el admin no queda afuera", r.status_code == 200, r.status_code)
r = cliente(logueado=False).get("/api/sesion-viva")
d = r.get_json(silent=True) or {}
check("sin sesión es un 401 pelado (las guías públicas no redirigen)",
      r.status_code == 401 and not d.get("relogin"), f"{r.status_code} {d}")
js = open(os.path.join(os.path.dirname(__file__), "..", "..",
                       "webService", "static", "sesion.js")).read()
check("el front escucha el volver al foco", "visibilitychange" in js and "focus" in js)
check("solo redirige con la marca relogin", "data.relogin" in js)
check("y no redirige dos veces", "yendoAlLogin" in js)
for tpl in ["index.html", "followers.html", "admin.html"]:
    html = open(os.path.join(os.path.dirname(__file__), "..", "..",
                             "webService", "templates", tpl)).read()
    check(f"{tpl} lo carga", "sesion.js" in html)

print("\n== 12 quinquies. El reloj del CRM NO manda a nadie al login ==")
# Era una pérdida de órdenes real: el front pide la hora y una línea después
# postea el envío de tráfico, en el mismo click. Si la hora contestaba 401 con
# relogin, el interceptor navegaba al login y el POST no llegaba a guardar nada.
r = cliente(cred=None).get("/api/server_time_ar")
d = r.get_json(silent=True) or {}
check("contesta 200, no un 401", r.status_code == 200, f"{r.status_code} {d}")
check("con la hora AR local como respaldo",
      bool(re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", d.get("ymdhmAR") or "")), d)
check("y avisa que es un respaldo", d.get("fallback") is True, d)
c = cliente(cred=None)
c.get("/api/server_time_ar")
with c.session_transaction() as s:
    check("y sobre todo: no le arranca la sesión al envío que viene atrás",
          s.get("logged_in") is True, dict(s))

print("\n== 12 sexies. El flujo REAL de tráfico guarda la orden ==")
# La secuencia exacta que hace _enviarTraficoAlCrm() en app.js: GET de la hora y
# POST del envío, en el mismo click.
ENCOLADAS.clear()
c = cliente(cred=None)
c.get("/api/server_time_ar")
r = c.post("/api/enviar_trafico", json={
    "url": "https://ig.com/p/x", "client": "vip", "disponible": 150,
    "ordenes": [{"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
                 "cant_inicial": "10", "cantidad": "10", "programado": 0,
                 "fecha_programada": None, "comentarios": [], "producto_id": "77"}]})
d = r.get_json(silent=True) or {}
check("manda al login", r.status_code == 401 and d.get("relogin") is True, d)
check("pero la orden de tráfico QUEDÓ GUARDADA",
      d.get("encolada") is True and ENCOLADAS, f"{r.status_code} {d}")

print("\n== 12 septies. Un 401 `relogin` NO deja sin rescate al POST que viene atrás ==")
# Esta es la propiedad general, independiente de qué endpoint la dispare: si la
# respuesta de relogin limpia la sesión, el request siguiente del mismo click
# llega sin cookie, muere en require_login y la orden se pierde. Se prueba con un
# GET que sí toca el CRM (productos), que es el caso que queda vivo ahora que el
# reloj dejó de patear.
ENCOLADAS.clear()
c = cliente(cred=None)
r = c.get("/api/productos?rrss=1")
d = r.get_json(silent=True) or {}
check("el GET del CRM sí manda al login",
      r.status_code == 401 and d.get("relogin") is True, f"{r.status_code} {d}")
r = c.post("/api/publicar", json={
    "url": "https://ig.com/p/x", "client": "vip", "comentarios": ["a", "b"],
    "ordenes": [{"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
                 "cant_inicial": "10", "cantidad": "10", "programado": 0,
                 "fecha_programada": None, "comentarios": [], "producto_id": "77"}]})
d = r.get_json(silent=True) or {}
check("y el POST que le sigue TODAVÍA guarda la orden",
      d.get("encolada") is True and ENCOLADAS, f"{r.status_code} {d}")

print("\n== 13. El next se respeta y no habilita un redirect a otro sitio ==")
r = cliente(cred=None).get("/followers?cliente=vip")
check("conserva la query de la pantalla", next_de(r).startswith("/followers"), destino(r))
check("y el cliente que estaba mirando", "cliente=vip" in next_de(r), destino(r))
# El login TIENE que salir bien: si falla, no hay Location y el check pasaba solo
# porque comparaba contra un string vacío (lo destapó la auditoría por mutación).
_auth_ok = web._authenticate
web._authenticate = lambda u, p: ({"user_id": 9, "account_id": 3, "username": u,
                                   "is_admin": False}, None)
_refresh_ok = web._refrescar_idvendedor
web._refrescar_idvendedor = lambda aid: None
try:
    for malo in ["https://malicioso.test/", "//malicioso.test/", "http://malicioso.test"]:
        r = web.app.test_client().post("/login", data={"username": "facu@growi.com",
                                                       "password": "y", "next": malo})
        check(f"el login redirige de verdad (si no, el check no prueba nada) [{malo}]",
              r.status_code in (302, 303) and destino(r), f"{r.status_code} {destino(r)}")
        check(f"y un next externo no se usa como destino [{malo}]",
              "malicioso" not in destino(r), destino(r))
finally:
    web._authenticate = _auth_ok
    web._refrescar_idvendedor = _refresh_ok

print("\n== 14. El chequeo corre ANTES de la vista, no dentro de cada una ==")
src = open(os.path.join(os.path.dirname(__file__), "..", "..",
                        "webService", "app.py")).read()
check("está como before_request", "def _exigir_sesion_crm" in src
      and src.split("def _exigir_sesion_crm")[0].rstrip().endswith("@app.before_request"))
check("y exime al login para no hacer bucle", "_SIN_SESION_CRM" in src)

print("\n== 15. Después de reloguearse vuelve a donde estaba ==")
_auth_original = web._authenticate
web._authenticate = lambda u, p: ({"user_id": 9, "account_id": 3, "username": u,
                                   "is_admin": False}, None)
web._refrescar_idvendedor = lambda aid: None
try:
    cli = cliente(cred=None)
    r = cli.get("/followers?cliente=vip")
    volver = next_de(r)
    html = cli.get(f"/login?motivo=sesion_crm&next={volver}").get_data(as_text=True)
    check("el formulario se lleva el next escondido", f'value="{volver}"' in html
          or volver in html, volver)
    r = cli.post("/login", data={"username": "facu@growi.com", "password": "x",
                                 "next": volver})
    check("y al entrar lo devuelve a su pantalla",
          urlparse(destino(r)).path == "/followers", destino(r))
    with cli.session_transaction() as s:
        check("con la contraseña de nuevo en memoria", bool(s.get("cred_key")), dict(s))
    check("y ahora sí la pantalla carga", cli.get("/followers").status_code == 200)
finally:
    web._authenticate = _auth_original

print("\n== 16. Casos raros: no revientan ni sirven de más ==")
# Valor EXACTO, no una tupla que acepta el comportamiento viejo y el nuevo a la vez.
r = cliente(cred=None).get("/una-ruta-que-no-existe")
check("una ruta inexistente con sesión rota va al login", r.status_code == 302, r.status_code)
r = cliente(cred="secreta").get("/una-ruta-que-no-existe")
check("y con sesión sana sigue siendo 404", r.status_code == 404, r.status_code)
r = cliente(cred=None).head("/")
check("HEAD de la home también manda al login", r.status_code == 302, r.status_code)
r = cliente(cred=None).head("/api/me")
check("HEAD sobre una API se comporta igual que su GET (200, sin guard)",
      r.status_code == 200, r.status_code)
r = cliente(cred=None).get("/static/app.js")
check("los estáticos se siguen sirviendo", r.status_code == 200, r.status_code)
r = cliente(logueado=False).get("/api/me")
check("sin cookie, el 401 es el de siempre (sin relogin)",
      r.status_code == 401 and not (r.get_json(silent=True) or {}).get("relogin"),
      r.get_json(silent=True))

print()
if FALLOS:
    print(f"FALLARON {len(FALLOS)}:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("TODO OK — sin sesión del CRM la página no se carga: va derecho al login")
