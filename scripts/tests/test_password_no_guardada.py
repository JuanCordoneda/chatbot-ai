"""La contraseña de Growi no se guarda en ningún lado.

Es la invariante del cambio, y es fácil de romper sin querer: alcanza con que
alguien vuelva a agregar la columna, la meta en la sesión de Flask "para que sea
más cómodo", o la deje viajar en la cookie de recordar usuario. Cualquiera de
esas tres la devuelve al disco, que es justo lo que se quiso evitar.

Corre sin DB ni CRM: todo lo de afuera está mockeado.

    PYTHONPATH=.:webService python3 scripts/tests/test_password_no_guardada.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webService"))

import app as web                                                   # noqa: E402
from common import models                                           # noqa: E402

FALLOS = []


def check(nombre, cond, extra=""):
    print(f"  {'OK   ' if cond else 'FALLA'} {nombre}" + (f"\n         → {extra}" if not cond and extra else ""))
    if not cond:
        FALLOS.append(nombre)


CUENTA = {"id": 3, "crm_email": "facu@growi.com", "crm_url": "https://crm.test",
          "crm_idvendedor": "77", "crm_idventa": "88", "crm_proxy": "",
          "crm_disponible": "100", "crm_password": ""}


class RepoFake:
    RepoError = RuntimeError

    @staticmethod
    def get_account_crm_config(account_id):
        return dict(CUENTA) if account_id == CUENTA["id"] else None


print("== 1. El modelo no tiene dónde guardarla ==")
cols = set(models.Account.__table__.columns.keys())
check("accounts no tiene columna de contraseña",
      not any("password" in c for c in cols), sorted(cols))

print("\n== 2. La config de la cuenta nunca trae contraseña de la DB ==")
web._repo = RepoFake
with web.app.test_request_context("/"):
    cfg = web._account_crm_cfg(CUENTA["id"])
check("crm_password viene vacía sin sesión con credencial", cfg.get("crm_password") == "", cfg)

print("\n== 3. Con la sesión del vendedor, sale de memoria ==")
with web.app.test_request_context("/") as ctx:
    from flask import session
    session["cred_key"] = web._guardar_credencial(CUENTA["id"], "secreta")
    cfg = web._account_crm_cfg(CUENTA["id"])
    check("la contraseña llega desde el almacén en memoria",
          cfg.get("crm_password") == "secreta", cfg.get("crm_password"))

    # La clave es de la cuenta 3; pedirla como cuenta 9 no debe prestarla.
    otra = web._credencial_de_sesion(9)
    check("no se presta la credencial a otra cuenta", otra == "", otra)

print("\n== 4. La cookie de sesión no lleva la contraseña ==")
cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["stamp"] = web.SESSION_STAMP
    s["account_id"] = CUENTA["id"]
    s["cred_key"] = web._guardar_credencial(CUENTA["id"], "secreta")
cookie = "".join(str(c) for c in cli.cookie_jar) if hasattr(cli, "cookie_jar") else ""
with cli.session_transaction() as s:
    contenido = json.dumps({k: str(v) for k, v in s.items()})
check("la sesión guarda una clave, no la contraseña",
      "secreta" not in contenido and "secreta" not in cookie, contenido)

print("\n== 5. La cookie de 'recordar usuario' tampoco ==")
with web.app.test_request_context("/"):
    resp = web._recordar_usuario(web.make_response(""), "facu@growi.com")
blob = resp.headers.get("Set-Cookie", "")
check("no viaja la contraseña ni cifrada ni en claro", "secreta" not in blob, blob[:80])
# Lo importante no es que el texto no aparezca (va cifrado), sino que el payload
# solo tenga el usuario. Se descifra y se mira.
from common.crypto import decrypt                                   # noqa: E402
valor = blob.split("=", 1)[1].split(";")[0] if "=" in blob else ""
datos = json.loads(decrypt(valor) or "{}")
check("el payload de la cookie es solo el usuario", set(datos) == {"u"}, datos)

print("\n== 6. Sin credencial en memoria, el envío corta con un mensaje claro ==")
try:
    web._growi_login_with({"crm_url": "https://crm.test", "crm_email": "x@y.com",
                           "crm_password": ""})
    check("levanta CredencialAusente", False, "no levantó nada")
except web.CredencialAusente as e:
    check("levanta CredencialAusente", True)
    check("y el mensaje le dice al vendedor qué hacer",
          "iniciar sesión" in str(e).lower(), str(e))
except Exception as e:
    check("levanta CredencialAusente", False, repr(e))

print("\n== 7. El motivo en pantalla no culpa a la contraseña ==")
codigo, criollo = web._motivo_envio_criollo(
    "CredencialAusente: Tu sesión ya no tiene la contraseña de Growi.", "")
check("se clasifica como sesión vencida, no como CRM que cortó",
      codigo == "sin_credencial", f"{codigo} / {criollo}")

print("\n== 8. El logout borra la contraseña de memoria ==")
with web.app.test_request_context("/"):
    from flask import session
    clave = web._guardar_credencial(CUENTA["id"], "secreta")
    session["cred_key"] = clave
    web._olvidar_credencial()
    check("la entrada desaparece del almacén", clave not in web._CREDENCIALES,
          list(web._CREDENCIALES))

print()
if FALLOS:
    print(f"FALLARON {len(FALLOS)}:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("TODO OK — la contraseña de Growi no queda guardada en ningún lado")
