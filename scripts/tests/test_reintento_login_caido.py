"""Una orden que se frena en el LOGIN se puede reintentar.

El agujero que cierra: cuando el CRM no nos abría la sesión, la orden se perdía.
El envío nunca salía —se frena antes de enviar_trafico.php— pero igual se
descartaban los comentarios ya generados, y en "Órdenes que no entraron" esa
orden aparecía sin botón de reintentar. El vendedor tenía que rehacer el post
entero por un problema que no era suyo ni de su orden.

La condición para guardar no es "fue un error de red" sino "no salió nada":
un login rechazado cumple, y un POST que salió y no sabemos si entró NO.

    PYTHONPATH=.:webService python3 scripts/tests/test_reintento_login_caido.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webService"))

import requests as _rq                                              # noqa: E402
import app as web                                                   # noqa: E402

FALLOS = []


def check(nombre, cond, extra=""):
    print(f"  {'OK   ' if cond else 'FALLA'} {nombre}" + (f"\n         → {extra}" if not cond and extra else ""))
    if not cond:
        FALLOS.append(nombre)


ENCOLADAS = []


class RepoCola:
    RepoError = RuntimeError

    @staticmethod
    def encolar_orden(post_url, payload, **kw):
        ENCOLADAS.append({"post_url": post_url, "payload": payload, **kw})
        return {"id": 900 + len(ENCOLADAS)}

    @staticmethod
    def get_account_crm_config(aid):
        return {"crm_email": "facu@growi.com", "crm_url": "https://crm.test",
                "crm_idvendedor": "77", "crm_idventa": "88"}

    # La pantalla de "órdenes que no entraron" lee de las dos fuentes.
    @staticmethod
    def list_growi_calls(aid, **kw):
        return [{"id": 1, "trace_id": "abc123", "created_at": "2026-08-11T10:00:00",
                 "error": "GrowiAuthError: el CRM no abrió la sesión",
                 "response_snippet": "", "post_url": "https://ig.com/p/x",
                 "client_ig_username": "vipsportslv", "idventa": None,
                 "costo": None, "status_code": None}]

    @staticmethod
    def list_pending_orders(aid, estados=None):
        return [{"id": 901, "created_at": "2026-08-11T10:00:00",
                 "post_url": "https://ig.com/p/x", "client_ig_username": "vipsportslv",
                 "ultimo_error": "GrowiAuthError: el CRM no abrió la sesión",
                 "estado": "fallida", "payload": {"trace_id": "abc123"}}]


web._repo = RepoCola

cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["stamp"] = web.SESSION_STAMP
    s["account_id"] = 3
    s["user_id"] = 9
    s["username"] = "facu"
    s["cred_key"] = web._guardar_credencial(3, "x")

ORDEN = {"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 1.0,
         "cant_inicial": "10", "cantidad": "10", "programado": 0,
         "fecha_programada": None, "comentarios": [], "producto_id": "77"}


def publicar():
    ENCOLADAS.clear()
    return cli.post("/api/publicar", json={
        "url": "https://ig.com/p/x", "client": "vipsportslv",
        "comentarios": ["lindo", "genial"], "ordenes": [dict(ORDEN)]})


def trafico():
    ENCOLADAS.clear()
    return cli.post("/api/enviar_trafico", json={
        "url": "https://ig.com/p/x", "client": "vipsportslv",
        "disponible": 150, "ordenes": [dict(ORDEN)]})


print("== 1. El CRM rechaza el login: la orden se guarda ==")


def login_rechazado(method, path, account_id=None, **kw):
    err = web.GrowiAuthError("El CRM no dejó entrar a facu@growi.com y rebotó al login")
    err.pre_envio = True
    raise err


web._growi_request = login_rechazado
r = publicar()
res = (r.get_json() or {}).get("resultado") or {}
check("la orden queda guardada", res.get("encolada") is True, res)
check("con los comentarios adentro",
      ENCOLADAS and ENCOLADAS[0]["payload"]["comentarios"] == ["lindo", "genial"],
      ENCOLADAS)
check("y el mensaje dice dónde reintentarla",
      "reintentala desde" in (res.get("errors") or [""])[0].lower(), res.get("errors"))

print("\n== 2. Sin la contraseña en memoria, igual se guarda ==")


def sin_credencial(method, path, account_id=None, **kw):
    raise web.CredencialAusente("Tu sesión ya no tiene la contraseña de Growi.")


web._growi_request = sin_credencial
r = publicar()
res = (r.get_json() or {}).get("resultado") or {}
check("la orden no se pierde por una sesión vencida", res.get("encolada") is True, res)

print("\n== 3. El tráfico también se guarda (antes nunca se encolaba) ==")
web._growi_request = login_rechazado
r = trafico()
d = r.get_json() or {}
check("la orden de tráfico queda guardada", d.get("encolada") is True, d)
check("con el disponible, que hace falta para reenviarla",
      ENCOLADAS and ENCOLADAS[0]["payload"]["disponible"] == 150, ENCOLADAS)

print("\n== 4. Lo que PUDO haber entrado NO se guarda ==")
# Es la invariante que protege la plata: enviar_trafico.php no es idempotente.


def corte_esperando(method, path, account_id=None, **kw):
    raise _rq.exceptions.ReadTimeout("se cortó esperando la respuesta")


web._growi_request = corte_esperando
r = publicar()
res = (r.get_json() or {}).get("resultado") or {}
check("no se guarda si el POST pudo haber salido", not res.get("encolada"), res)
check("y se le avisa que revise en Growi",
      "revisá en growi" in (res.get("errors") or [""])[0].lower(), res.get("errors"))

print("\n== 5. El fallo se muestra UNA vez, no dos ==")
# La orden guardada y su fila de auditoría son el mismo fallo. Si se listan las
# dos, el vendedor ve una con botón y otra sin, y parece que rebotaron dos.
r = cli.get("/api/mis-envios-fallidos")
envios = (r.get_json() or {}).get("envios") or []
check("aparece una sola entrada", len(envios) == 1, envios)
check("y es la que se puede reintentar",
      envios and envios[0].get("reintentable") is True, envios)

print("\n== 6. Si NO se guardó la orden, la fila de auditoría sí se muestra ==")
# El otro lado del filtro: sin orden guardada, esa fila es lo único que tiene el
# vendedor para enterarse de que rebotó. No se puede esconder.
RepoCola.list_pending_orders = staticmethod(lambda aid, estados=None: [])
r = cli.get("/api/mis-envios-fallidos")
envios = (r.get_json() or {}).get("envios") or []
check("la fila sigue estando", len(envios) == 1, envios)
check("pero sin botón, porque no hay payload que reenviar",
      envios and envios[0].get("reintentable") is False, envios)

print()
if FALLOS:
    print(f"FALLARON {len(FALLOS)}:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("TODO OK — un login caído ya no hace perder la orden")
