"""Quedarse sin la contraseña del CRM manda al login, no a un cartel muerto.

El agujero: la contraseña del CRM vive en memoria, pero la sesión de Flask no.
Cuando se pierde la primera (reinicio del servicio, sesión vencida), `logged_in`
sigue puesto y `require_login` deja pasar, así que el backend contestaba un error
común y el vendedor leía "volvé a iniciar sesión" en un cartel... sin que nada lo
llevara ahí. Podía quedarse apretando Publicar para siempre.

    PYTHONPATH=.:webService python3 scripts/tests/test_relogin.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webService"))

import app as web                                                   # noqa: E402

FALLOS = []


def check(nombre, cond, extra=""):
    print(f"  {'OK   ' if cond else 'FALLA'} {nombre}" + (f"\n         → {extra}" if not cond and extra else ""))
    if not cond:
        FALLOS.append(nombre)


ENCOLADAS = []


class RepoFake:
    RepoError = RuntimeError

    @staticmethod
    def encolar_orden(post_url, payload, **kw):
        ENCOLADAS.append(payload)
        return {"id": 77}

    @staticmethod
    def get_account_crm_config(aid):
        return {"crm_email": "facu@growi.com", "crm_url": "https://crm.test",
                "crm_idvendedor": "77", "crm_idventa": "88"}


web._repo = RepoFake

ORDEN = {"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
         "cant_inicial": "10", "cantidad": "10", "programado": 0,
         "fecha_programada": None, "comentarios": [], "producto_id": "77"}


def cliente_logueado_sin_credencial():
    """Sesión válida (entró bien) pero SIN la contraseña en memoria: es
    exactamente lo que queda tras un reinicio del web-service."""
    c = web.app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["stamp"] = web.SESSION_STAMP
        s["account_id"] = 3
        s["user_id"] = 9
        s["username"] = "facu"
        # sin cred_key: ese es el punto
    return c


print("== 1. Publicar sin credencial → 401 con relogin ==")
ENCOLADAS.clear()
cli = cliente_logueado_sin_credencial()
r = cli.post("/api/publicar", json={"url": "https://ig.com/p/x", "client": "vip",
                                    "comentarios": ["a", "b"], "ordenes": [dict(ORDEN)]})
d = r.get_json() or {}
check("contesta 401 y no un error común", r.status_code == 401, r.status_code)
check("con la marca que el front usa para redirigir", d.get("relogin") is True, d)
check("y le dice que vuelva a entrar", "volvé a entrar" in (d.get("error") or "").lower()
      or "iniciar sesión" in (d.get("error") or "").lower(), d.get("error"))

print("\n== 2. La orden NO se pierde: se guarda antes de mandarlo al login ==")
check("quedó guardada", d.get("encolada") is True, d)
check("con los comentarios ya generados",
      ENCOLADAS and ENCOLADAS[0]["comentarios"] == ["a", "b"], ENCOLADAS)
check("y el mensaje se lo dice", "guardamos la orden" in (d.get("error") or "").lower(),
      d.get("error"))

print("\n== 3. La sesión NO se limpia acá: la limpia el login ==")
# Antes esta respuesta hacía session.clear(), y eso costaba órdenes: el request
# siguiente del MISMO click (el POST de publicar / enviar_trafico) llegaba sin
# cookie, moría en require_login con "No autenticado" y no alcanzaba el
# `except CredencialAusente` que guarda la orden. De paso le borraba la sesión a
# las otras pestañas, cuyos 401 pasaban a ser "No autenticado" pelados —sin la
# marca `relogin`—, así que nadie las llevaba al login: quedaban colgadas.
# La sesión sigue siendo una identidad válida; lo que falta es la contraseña del
# CRM. Quien corta es el guard al navegar, y el POST del login pisa todo al entrar.
with cli.session_transaction() as s:
    check("la sesión sobrevive para que el rescate funcione",
          s.get("logged_in") is True, dict(s))
# Lo que importa de verdad es que el front tenga con qué redirigir.
check("y la respuesta trae la marca que lo lleva al login", d.get("relogin") is True, d)

print("\n== 4. Tráfico: mismo trato ==")
ENCOLADAS.clear()
cli = cliente_logueado_sin_credencial()
r = cli.post("/api/enviar_trafico", json={"url": "https://ig.com/p/x", "client": "vip",
                                          "disponible": 150, "ordenes": [dict(ORDEN)]})
d = r.get_json() or {}
check("contesta 401 con relogin", r.status_code == 401 and d.get("relogin") is True, d)
check("y también guarda la orden", d.get("encolada") is True, d)

print("\n== 5. Cualquier otro endpoint del CRM también manda al login ==")
# La red de contención: son más de veinte los endpoints que hablan con el CRM y
# ninguno debería tener que acordarse de capturar esto.
cli = cliente_logueado_sin_credencial()
r = cli.get("/api/productos?rrss=1")
d = r.get_json() or {}
check("productos manda al login", r.status_code == 401 and d.get("relogin") is True,
      f"{r.status_code} {d}")

print("\n== 6. El login explica por qué lo mandaron ahí ==")
cli = web.app.test_client()
html = cli.get("/login?motivo=sesion_crm").get_data(as_text=True)
check("avisa que la sesión venció", "venció" in html.lower(), html[:200])
check("y le dice dónde quedó la orden", "no entraron" in html.lower())
check("como aviso, no como error rojo", "login-error--info" in html)
# Sin el motivo, el login es el de siempre.
html = cli.get("/login").get_data(as_text=True)
check("sin motivo no inventa ningún aviso", "venció" not in html.lower())

print("\n== 7. El interceptor del front está puesto ==")
js = open(os.path.join(os.path.dirname(__file__), "..", "..",
                       "webService", "static", "app.js")).read()
check("intercepta fetch globalmente", "window.fetch = async" in js)
check("mira la marca relogin", "data.relogin" in js)
check("y redirige al login", "/login?motivo=sesion_crm" in js)
check("clona la respuesta para no romper a quien llamó", "r.clone()" in js)
check("y redirige una sola vez", "yendoAlLogin" in js)

print()
if FALLOS:
    print(f"FALLARON {len(FALLOS)}:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("TODO OK — la sesión vencida lleva al login y no se lleva puesta la orden")
