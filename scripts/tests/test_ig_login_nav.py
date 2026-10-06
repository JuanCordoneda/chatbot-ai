"""
Login con navegador (modules/ig_login_nav) contra un Instagram falso local.

Levanta un server HTTP que imita las pantallas que importan (login, código de
verificación, «revisá tu otro dispositivo») y maneja un Chromium de verdad
contra él. Lo que tiene que valer:

  - login directo → cookies con sessionid;
  - pantalla de código: un código malo no mata el login, uno bueno entra;
  - pantalla de «Fui yo» sin campo: Confirmar sin código entra cuando se aprobó;
  - contraseña mala se distingue;
  - la contraseña no aparece en los logs.

Necesita playwright + chromium instalados; si no están, se saltea.
"""
import contextlib
import http.server
import io
import os
import sys
import threading
from urllib.parse import parse_qs

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _ruta in ("/app", RAIZ, os.path.join(RAIZ, "openAIService")):
    if os.path.isdir(_ruta) and _ruta not in sys.path:
        sys.path.insert(0, _ruta)

from modules import ig_login_nav  # noqa: E402

if not ig_login_nav.disponible():
    print("playwright no está instalado: salteo")
    sys.exit(0)

PASSWORD = "clave-super-secreta-123"
# usuario → qué pantalla muestra Instagram después de la contraseña
CUENTAS = {"directa": "ok", "concodigo": "codigo", "aprobar": "aprobar"}
APROBADAS = set()

LOGIN = b"""<html><body><form method=post action=/accounts/login/>
<input name=username autocomplete=username><input name=password type=password>
<button type=submit>Entrar</button></form></body></html>"""
CODIGO = """<html><body><h2>Ingresa el codigo que enviamos a j***@gmail.com</h2>{err}
<form method=post><input name=code autocomplete=one-time-code inputmode=numeric>
<button type=submit>Continuar</button></form></body></html>"""
APROBAR = b"""<html><body><h2>Revisa tus notificaciones en otro dispositivo</h2>
<script>setInterval(()=>fetch('/estado').then(r=>r.text()).then(t=>{if(t==='si')location='/'}),500)</script>
</body></html>"""


class IG(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _html(self, cuerpo, cookies=(), status=200, location=None):
        self.send_response(status)
        for c in cookies:
            self.send_header("Set-Cookie", c + "; Path=/")
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(cuerpo if isinstance(cuerpo, bytes) else cuerpo.encode())

    def _usuario(self):
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "u":
                return v
        return ""

    def do_GET(self):
        if self.path.startswith("/accounts/login"):
            return self._html(LOGIN, cookies=["csrftoken=t", "mid=m1"])
        if self.path.startswith("/auth_platform/codeentry"):
            return self._html(CODIGO.format(err=""))
        if self.path.startswith("/auth_platform/aprobar"):
            return self._html(APROBAR)
        if self.path == "/estado":
            return self._html("si" if self._usuario() in APROBADAS else "no")
        if self.path == "/":
            ok = self._usuario() in APROBADAS
            return self._html("<html><body>Inicio</body></html>",
                              cookies=["sessionid=99%3Aok"] if ok else [])
        self._html("no", status=404)

    def do_POST(self):
        datos = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if self.path.startswith("/accounts/login"):
            u = datos.get("username", [""])[0]
            if datos.get("password", [""])[0] != PASSWORD or u not in CUENTAS:
                return self._html(LOGIN + "<p>La contraseña que ingresaste es incorrecta.</p>".encode())
            pantalla = CUENTAS[u]
            if pantalla == "ok":
                return self._html(b"", cookies=["sessionid=1%3Adirecta", "u=" + u],
                                  status=302, location="/")
            destino = "/auth_platform/codeentry/?apc=TOKEN" if pantalla == "codigo" else "/auth_platform/aprobar/"
            return self._html(b"", cookies=["u=" + u], status=302, location=destino)
        if self.path.startswith("/auth_platform/codeentry"):
            if datos.get("code", [""])[0] == "654321":
                APROBADAS.add(self._usuario())
                return self._html(b"", status=302, location="/")
            return self._html(CODIGO.format(err="<p>Ese codigo no es valido</p>"))
        self._html("no", status=404)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), IG)
threading.Thread(target=server.serve_forever, daemon=True).start()
ig_login_nav._BASE = f"http://127.0.0.1:{server.server_port}"
ig_login_nav._DOMINIO = "127.0.0.1"
ig_login_nav._proxy = lambda: None

fallas = []


def chequear(que, condicion, detalle=""):
    print(("  ok   " if condicion else "  FALLA") + f"  {que}" + (f" — {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallas.append(que)


logs = io.StringIO()
with contextlib.redirect_stdout(logs):
    r_directa = ig_login_nav.iniciar("@Directa", PASSWORD)
    r_mala = ig_login_nav.iniciar("directa", "otra")
    r1 = ig_login_nav.iniciar("concodigo", PASSWORD)
    r2 = ig_login_nav.confirmar_codigo(r1.get("login_id", ""), "111 111") if r1.get("login_id") else {}
    r3 = ig_login_nav.confirmar_codigo(r1.get("login_id", ""), "654321") if r1.get("login_id") else {}
    a1 = ig_login_nav.iniciar("aprobar", PASSWORD)
    a2 = ig_login_nav.confirmar_codigo(a1.get("login_id", ""), "") if a1.get("login_id") else {}
    APROBADAS.add("aprobar")
    a3 = ig_login_nav.confirmar_codigo(a1.get("login_id", ""), "") if a1.get("login_id") else {}

print("login directo")
chequear("entra con sessionid", r_directa.get("paso") == "ok"
         and r_directa["cookies"].get("sessionid") == "1%3Adirecta", str(r_directa))
chequear("contraseña mala se distingue", r_mala.get("paso") == "error"
         and "contraseña" in r_mala.get("detalle", ""), str(r_mala))

print("pantalla de código")
chequear("pide código y dice a dónde", r1.get("paso") == "codigo" and r1.get("destino") == "j***@gmail.com", str(r1))
chequear("código malo: error pero el login sigue", r2.get("paso") == "error" and r2.get("login_id") == r1.get("login_id"), str(r2))
chequear("código bueno entra", r3.get("paso") == "ok" and r3["cookies"].get("sessionid") == "99%3Aok", str(r3))
chequear("después se olvida el login", r1.get("login_id") not in ig_login_nav._pendientes)

print("«Fui yo» sin campo de código")
chequear("pide aprobar", a1.get("paso") == "codigo" and a1.get("metodo") == "aprobar", str(a1))
chequear("sin aprobar todavía: avisa y sigue", a2.get("paso") == "error" and a2.get("login_id"), str(a2))
chequear("aprobado: entra", a3.get("paso") == "ok" and a3["cookies"].get("sessionid"), str(a3))

print("secretos")
chequear("la contraseña nunca se imprime", PASSWORD not in logs.getvalue())
chequear("ni el token de la pantalla de código", "TOKEN" not in logs.getvalue())
print()
print(logs.getvalue())
server.shutdown()
if fallas:
    print(f"FALLARON {len(fallas)}:", *fallas, sep="\n  - ")
    sys.exit(1)
print("TODO OK")
