"""
Navegador a mano (modules/ig_remoto) contra un Instagram falso local.

Se maneja como desde el celular: tocar por coordenadas, escribir, Enter. Lo
que tiene que valer:

  - abrir devuelve una captura;
  - toques y texto llegan a la página;
  - en una pantalla de verificación (suspended) no se ofrece la sesión aunque
    ya haya sessionid; al salir de ahí, sí, y una sola vez hasta otra acción;
  - lo tipeado no aparece en los logs.

Necesita playwright + chromium; si no están, se saltea.
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

from modules import ig_login_nav, ig_remoto  # noqa: E402

if not ig_login_nav.disponible():
    print("playwright no está instalado: salteo")
    sys.exit(0)

PASSWORD = "clave-super-secreta-123"
CAMPO = "position:absolute;left:20px;width:300px;height:30px;"
LOGIN = f"""<html><body style="margin:0"><form method=post action=/accounts/login/>
<input name=username style="{CAMPO}top:100px"><input name=password type=password style="{CAMPO}top:200px">
<button type=submit style="position:absolute;top:300px">Entrar</button></form></body></html>"""
SUSPENDIDA = f"""<html><body style="margin:0"><p>Confirma que eres una persona real</p>
<form method=post action=/accounts/suspended/><button style="{CAMPO}top:400px">Continuar</button></form></body></html>"""


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
        self.wfile.write(cuerpo.encode())

    def do_GET(self):
        if self.path.startswith("/accounts/login"):
            return self._html(LOGIN, cookies=["csrftoken=t"])
        if self.path.startswith("/accounts/suspended"):
            return self._html(SUSPENDIDA)
        return self._html("<html><body>Inicio</body></html>")

    def do_POST(self):
        datos = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        if self.path.startswith("/accounts/login"):
            ok = (datos.get("username", [""])[0] == "cuenta"
                  and datos.get("password", [""])[0] == PASSWORD)
            if not ok:
                return self._html(LOGIN + "<p>mal</p>")
            # Como Instagram: sessionid ya, pero primero la pantalla de «persona real».
            return self._html("", cookies=["sessionid=5%3Ax", "ds_user_id=5"], status=302,
                              location="/accounts/suspended/")
        if self.path.startswith("/accounts/suspended"):
            return self._html("", status=302, location="/")
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
    a = ig_remoto.abrir("@Cuenta")
    rid = a.get("id", "")
    ig_remoto.accion(rid, {"tipo": "tap", "x": 100, "y": 115})
    ig_remoto.accion(rid, {"tipo": "texto", "texto": "cuenta"})
    ig_remoto.accion(rid, {"tipo": "tap", "x": 100, "y": 215})
    ig_remoto.accion(rid, {"tipo": "texto", "texto": PASSWORD})
    en_suspended = ig_remoto.accion(rid, {"tipo": "tecla", "tecla": "Enter"})
    mirar = ig_remoto.accion(rid, {"tipo": "mirar"})
    salir = ig_remoto.accion(rid, {"tipo": "tap", "x": 100, "y": 415})
    otra = ig_remoto.accion(rid, {"tipo": "mirar"})
    ig_remoto.cerrar(rid)
    import time
    time.sleep(1.5)
    cerrada = ig_remoto.accion(rid, {"tipo": "mirar"})

chequear("abrir devuelve id y captura", rid and len(a.get("img", "")) > 1000, str({k: v for k, v in a.items() if k != "img"}))
chequear("en la pantalla de «persona real» no ofrece la sesión",
         en_suspended.get("ruta", "").startswith("/accounts/suspended") and "cookies" not in en_suspended,
         str({k: v for k, v in en_suspended.items() if k != "img"}))
chequear("mirando sin hacer nada tampoco", "cookies" not in mirar)
chequear("al salir de la verificación, ofrece la sesión con sessionid",
         (salir.get("cookies") or {}).get("sessionid") == "5%3Ax",
         str({k: v for k, v in salir.items() if k != "img"}))
chequear("una sola vez hasta otra acción", "cookies" not in otra)
chequear("cerrada avisa", cerrada.get("cerrada") is True, str(cerrada))
chequear("lo tipeado no aparece en los logs", PASSWORD not in logs.getvalue())
print()
print(logs.getvalue())
server.shutdown()
if fallas:
    print(f"FALLARON {len(fallas)}:", *fallas, sep="\n  - ")
    sys.exit(1)
print("TODO OK")
