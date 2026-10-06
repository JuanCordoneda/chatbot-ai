"""
Login a Instagram desde el panel (modules/ig_login), con Instagram guionado.

Lo que tiene que valer para que renovar la sesión desde el celular sirva:

  - un login directo termina en cookies con sessionid;
  - si Instagram pide código, el login queda pendiente y un código mal tipeado
    NO obliga a volver a poner la contraseña;
  - el "¿fuiste vos?" (checkpoint) y la contraseña mala se distinguen, porque se
    arreglan distinto;
  - la contraseña nunca aparece en lo que se imprime (los logs de Railway);
  - todo sale por _ig_req, o sea por el proxy del scraper.
"""
import contextlib
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _ruta in ("/app", RAIZ, os.path.join(RAIZ, "openAIService")):
    if os.path.isdir(_ruta) and _ruta not in sys.path:
        sys.path.insert(0, _ruta)

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido

from modules import ig_login                                        # noqa: E402

fallas = []
PASSWORD = "clave-super-secreta-123"


def chequear(que, condicion, detalle=""):
    print(("  ok   " if condicion else "  FALLA") + f"  {que}" + (f" — {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallas.append(que)


class Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no es json")
        return self._payload


LLAMADAS = []


def guion(pasos):
    """pasos: {fragmento de url: [(status, json, cookies_que_setea), ...]}.
    Cada llamada a esa url consume el siguiente paso."""
    colas = {k: list(v) for k, v in pasos.items()}

    def falso_ig_req(fn, url, **kw):
        sesion = fn.__self__
        LLAMADAS.append({"metodo": fn.__name__, "url": url, **kw})
        # Gana el fragmento más largo: "/accounts/login/" también está
        # adentro de la url del POST del login.
        for frag, cola in sorted(colas.items(), key=lambda kv: -len(kv[0])):
            if frag in url:
                status, payload, cookies = cola.pop(0)
                for k, v in cookies.items():
                    sesion.cookies.set(k, v, domain=".instagram.com")
                return Resp(status, payload)
        raise AssertionError(f"llamada no guionada: {url}")

    ig_login._ig_req = falso_ig_req


PAGINA = {"instagram.com/accounts/login/": [(200, None, {"csrftoken": "tok1", "mid": "m1"})]}

# ── 1. Login directo ──────────────────────────────────────────────────────────
print("login directo")
LLAMADAS.clear()
guion({**PAGINA, "/login/ajax/": [(200, {"authenticated": True, "user": True, "status": "ok"},
                                   {"sessionid": "99%3Aabc", "ds_user_id": "99"})]})
salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    r = ig_login.iniciar("@Los.Chicos.LOL", PASSWORD)
chequear("devuelve ok con el sessionid", r.get("paso") == "ok" and r["cookies"].get("sessionid") == "99%3Aabc", str(r))
chequear("trae también csrftoken y mid", r["cookies"].get("csrftoken") == "tok1" and r["cookies"].get("mid") == "m1")
post = [c for c in LLAMADAS if c["metodo"] == "post"][0]
chequear("manda el csrftoken de la página en el header", post["headers"].get("x-csrftoken") == "tok1")
chequear("normaliza el usuario (sin @, minúsculas)", post["data"]["username"] == "los.chicos.lol")
chequear("contraseña en el sobre que espera la web",
         post["data"]["enc_password"].startswith("#PWD_INSTAGRAM_BROWSER:0:")
         and post["data"]["enc_password"].endswith(":" + PASSWORD))
chequear("no sigue redirects (un redirect es un rechazo, no un éxito)", post.get("allow_redirects") is False)

# ── 2. csrftoken que no viene como cookie ────────────────────────────────────
print("csrftoken desde shared_data")
LLAMADAS.clear()
guion({"instagram.com/accounts/login/": [(200, None, {})],
       "shared_data": [(200, {"config": {"csrf_token": "tok2"}}, {})],
       "/login/ajax/": [(200, {"authenticated": True}, {"sessionid": "1%3Ax"})]})
r = ig_login.iniciar("x", PASSWORD)
post = [c for c in LLAMADAS if c["metodo"] == "post"][0]
chequear("usa el token de shared_data", post["headers"].get("x-csrftoken") == "tok2", str(post["headers"]))

# ── 3. 2FA, con un código mal tipeado en el medio ─────────────────────────────
print("2FA")
guion({**PAGINA,
       "/login/ajax/two_factor/": [
           (400, {"message": "Please check the security code and try again.", "status": "fail"}, {}),
           (200, {"authenticated": True, "user": True}, {"sessionid": "7%3Azz"}),
       ],
       "/login/ajax/": [(400, {"two_factor_required": True, "two_factor_info": {
           "username": "cuenta", "two_factor_identifier": "IDENT", "sms_two_factor_on": True,
           "totp_two_factor_on": False, "obfuscated_phone_number": "** 55"}}, {})]})
r = ig_login.iniciar("cuenta", PASSWORD)
chequear("pide código por SMS", r.get("paso") == "codigo" and r.get("metodo") == "sms" and r.get("login_id"), str(r))
chequear("dice a qué teléfono", r.get("destino") == "** 55")
lid = r["login_id"]
LLAMADAS.clear()
r2 = ig_login.confirmar_codigo(lid, "12 34 56")
chequear("código malo: error, pero el login sigue vivo", r2.get("paso") == "error" and r2.get("login_id") == lid, str(r2))
r3 = ig_login.confirmar_codigo(lid, "654321")
chequear("código bueno: ok con sessionid", r3.get("paso") == "ok" and r3["cookies"].get("sessionid") == "7%3Azz", str(r3))
cod = [c for c in LLAMADAS if "two_factor" in c["url"]]
chequear("manda identifier y el método SMS (1)",
         cod[0]["data"]["identifier"] == "IDENT" and cod[0]["data"]["verification_method"] == "1")
chequear("limpia los espacios del código", cod[0]["data"]["verificationCode"] == "123456")
chequear("una vez usado, el login pendiente se borra", ig_login.confirmar_codigo(lid, "1").get("paso") == "error"
         and "venció" in ig_login.confirmar_codigo(lid, "1").get("detalle", ""))

# ── 4. TOTP ───────────────────────────────────────────────────────────────────
guion({**PAGINA, "/login/ajax/": [(400, {"two_factor_required": True, "two_factor_info": {
    "two_factor_identifier": "I2", "totp_two_factor_on": True}}, {})],
    "two_factor": [(200, {"authenticated": True}, {"sessionid": "3%3Aq"})]})
r = ig_login.iniciar("otra", PASSWORD)
chequear("con app de autenticación pide ese código", r.get("metodo") == "app", str(r))

# ── 5. Checkpoint, contraseña mala, cuenta inexistente, rate limit ────────────
print("rechazos")
guion({**PAGINA, "/login/ajax/": [(400, {"message": "checkpoint_required",
                                         "checkpoint_url": "/challenge/x/", "status": "fail"}, {})]})
r = ig_login.iniciar("cuenta", PASSWORD)
chequear("checkpoint se distingue y pide «Fui yo»", r.get("paso") == "checkpoint" and "Fui yo" in r.get("detalle", ""), str(r))

# Después del «Fui yo» se reintenta: tiene que salir del MISMO dispositivo
# (mismas cookies), si no Instagram lo ve como otro y vuelve a pedir «Fui yo».
LLAMADAS.clear()
guion({"instagram.com/accounts/login/": [(200, None, {"csrftoken": "tok1"})],
       "/login/ajax/": [(200, {"authenticated": True}, {"sessionid": "5%3Aw"})]})
r = ig_login.iniciar("cuenta", PASSWORD)
post = [c for c in LLAMADAS if c["metodo"] == "post"][0]
chequear("el reintento tras «Fui yo» usa el mismo tarro (conserva el mid)",
         r.get("paso") == "ok" and r["cookies"].get("mid") == "m1", str(r))
chequear("y una vez adentro lo olvida", "cuenta" not in ig_login._dispositivos)

guion({**PAGINA, "/login/ajax/": [(200, {"user": True, "authenticated": False, "status": "ok"}, {})]})
r = ig_login.iniciar("cuenta", PASSWORD)
chequear("contraseña mala", r.get("paso") == "error" and "contraseña" in r.get("detalle", ""), str(r))

guion({**PAGINA, "/login/ajax/": [(200, {"user": False, "authenticated": False}, {})]})
r = ig_login.iniciar("noexiste", PASSWORD)
chequear("cuenta inexistente", r.get("paso") == "error" and "@noexiste" in r.get("detalle", ""), str(r))

guion({**PAGINA, "/login/ajax/": [(400, {"message": "Please wait a few minutes before you try again.",
                                         "status": "fail"}, {})]})
salida2 = io.StringIO()
with contextlib.redirect_stdout(salida2):
    r = ig_login.iniciar("cuenta", PASSWORD)
chequear("rate limit se explica", r.get("paso") == "error" and "minutos" in r.get("detalle", ""), str(r))

guion({**PAGINA, "/login/ajax/": [(500, {"raro": 1}, {})]})
salida3 = io.StringIO()
with contextlib.redirect_stdout(salida3):
    r = ig_login.iniciar("cuenta", PASSWORD)
chequear("respuesta desconocida no revienta", r.get("paso") == "error", str(r))

chequear("sin usuario o contraseña ni sale a la red",
         ig_login.iniciar("", PASSWORD)["paso"] == "error" and ig_login.iniciar("x", "")["paso"] == "error")

# ── 6. La contraseña no se filtra ─────────────────────────────────────────────
print("secretos")
todo = salida.getvalue() + salida2.getvalue() + salida3.getvalue()
chequear("la contraseña nunca se imprime", PASSWORD not in todo)
chequear("los logins pendientes no guardan la contraseña",
         all(PASSWORD not in repr(v) for v in ig_login._pendientes.values()))

print()
if fallas:
    print(f"FALLARON {len(fallas)}:", *fallas, sep="\n  - ")
    sys.exit(1)
print("TODO OK")
