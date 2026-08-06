"""Servidor de capturas para la guía de onboarding (/ayuda).

Sirve los templates REALES del panel (mismo HTML, mismo CSS, mismo JS) pero con
las respuestas de /api/* falseadas: así las capturas salen de la interfaz de
verdad y con clientes inventados, sin tocar la base ni el CRM ni exponer datos
de un cliente real.

    python3 harness.py 8899        # y después: python3 shoot.py
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from jinja2 import Environment, FileSystemLoader

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "webService")
env = Environment(loader=FileSystemLoader(os.path.join(WEB, "templates")))

CLIENTS = [
    {"id": 1, "ig_username": "peterjfournier", "display_name": "Peter Fournier", "status": "active",
     "gender": "male", "quality": "pro", "keyword_mode": False, "crm_idventa": "8811",
     "prompt": ("Actuá como seguidores reales de un entrenador de fitness. Comentarios cortos "
                "(máximo 8 palabras), variados, en el idioma del post, con buena onda y algo "
                "concreto de lo que se ve en el video.\n\nNunca: “nice post”, "
                "“great content”, hashtags, links, ni preguntar precios."),
     "ranges": {"comentarios": {"verificados": {"min": 6, "max": 10},
                                "comunes": {"min": 20, "max": 40}},
                "likes": [{"min": 300, "max": 600, "prod_id": "101"}],
                "views": [{"min": 2000, "max": 3500, "prod_id": "201"}]}},
    {"id": 2, "ig_username": "luciaramos", "display_name": "Lucía Ramos", "status": "active",
     "gender": "female", "quality": "standard", "keyword_mode": False, "crm_idventa": "8812",
     "prompt": "Comentarios de clientas reales de un estudio de pilates. Cálidos, breves, sin emojis.",
     "ranges": {"comentarios": {"verificados": {"min": 4, "max": 8},
                                "comunes": {"min": 15, "max": 30}},
                "likes": [{"min": 200, "max": 400, "prod_id": "102"}]}},
    {"id": 3, "ig_username": "estudiobora", "display_name": "Estudio Bora", "status": "active",
     "gender": None, "quality": "standard", "keyword_mode": False, "crm_idventa": "",
     "prompt": "", "ranges": {}},
]

VENTAS = [
    {"idventa": "8811", "idvendedor": "12", "nombre": "Campaña Fournier · agosto",
     "ig_username": "peterjfournier", "disponible": "84300", "monto": "120000",
     "vendedor": "Juan", "estado": "1", "activa": True, "fecha": "2026-08-01"},
    {"idventa": "8812", "idvendedor": "12", "nombre": "Campaña Ramos · agosto",
     "ig_username": "luciaramos", "disponible": "32100", "monto": "60000",
     "vendedor": "Juan", "estado": "1", "activa": True, "fecha": "2026-08-01"},
    {"idventa": "8813", "idvendedor": "12", "nombre": "Campaña Bora · julio",
     "ig_username": "estudiobora", "disponible": "9800", "monto": "40000",
     "vendedor": "Juan", "estado": "1", "activa": False, "fecha": "2026-07-02"},
]

PRODUCTOS = {
    "Likes": [{"id": 101, "label": "Likes Premium"}, {"id": 102, "label": "Likes Estándar"}],
    "Views": [{"id": 201, "label": "Views Reels"}],
    "Saves": [{"id": 301, "label": "Guardados"}],
    "Shares": [{"id": 401, "label": "Compartidos"}],
    "Reach": [{"id": 501, "label": "Alcance"}],
}


def api_response(path):
    if path.startswith("/api/admin/clients"):
        return {"clients": CLIENTS}
    if path.startswith("/api/ventas"):
        return {"ventas": VENTAS}
    if path.startswith("/api/productos"):
        return PRODUCTOS
    if path.startswith("/api/ordenes-pendientes"):
        return {"ordenes": []}
    if path.startswith("/api/prompt-requests"):
        return {"requests": []}
    if path.startswith("/api/me"):
        return {"username": "juan", "is_admin": False}
    return {}


# Se inyecta antes del JS de la app. Prepara la escena de cada captura (?shot=)
# una vez que la interfaz terminó de renderizar.
SHOT_JS = r"""
<script>
const _p = new URLSearchParams(location.search);
window.__shot = _p.get("shot") || "";
// Las capturas se sacan en los dos temas: la guía muestra la que corresponda.
if (_p.get("tema") === "light") {
  document.addEventListener("DOMContentLoaded", () => document.body.classList.add("light"));
}
window.addEventListener("load", () => setTimeout(() => {
  const s = window.__shot;
  // Deja en la página SOLO el elemento pedido, con un margen parejo alrededor.
  const solo = (el, pad) => {
    if (!el) return;
    const w = document.createElement("div");
    w.style.cssText = "padding:" + (pad || 26) + "px;background:var(--bg);";
    document.body.innerHTML = "";
    document.body.style.cssText = "background:var(--bg);margin:0;";
    w.appendChild(el);
    document.body.appendChild(w);
    // El .app del panel escala todo un 20%: sin esa clase el recorte sale más
    // chico que en la pantalla real.
    w.classList.add("app");
  };

  if (!s.startsWith("ficha")) return;             // la pantalla entera

  openClientModal(s === "ficha-nueva" ? undefined : 1);
  setTimeout(() => {
    const modal = document.querySelector("#client-mo .ax-modal");
    if (s === "ficha" || s === "ficha-nueva") { modal.style.maxHeight = "none"; solo(modal); return; }
    // El editor del prompt sólo existe a pantalla completa: el modal normal
    // muestra el teaser, no el textarea.
    if (s === "ficha:prompt") {
      togglePromptFull();
      setTimeout(() => { modal.style.height = "620px"; solo(modal); }, 250);
      return;
    }
    const que = s.split(":")[1];
    const mapa = {
      identidad: ".ax-sec--ident",
      comentarios: ".ax-sec--coment",
      trafico: ".ax-sec--trafico",
    };
    const el = que === "calidad"
      ? document.getElementById("client-quality").closest(".ax-field")
      : modal.querySelector(mapa[que]);
    // De Comentarios interesa sólo el bloque de cantidades: con la sección
    // entera entra también el prompt y el foco se pierde.
    solo(que === "comentarios" ? el.querySelector(".ax-field:last-child") : el);
  }, 700);
}, 500));
</script>
"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path.startswith("/api/"):
            return self._send(200, json.dumps(api_response(self.path)), "application/json")

        if path.startswith("/static/"):
            f = os.path.join(WEB, path.lstrip("/"))
            if not os.path.isfile(f):
                return self._send(404, "no", "text/plain")
            ctype = ("text/css" if f.endswith(".css") else
                     "application/javascript" if f.endswith(".js") else
                     "image/png" if f.endswith(".png") else "image/jpeg")
            with open(f, "rb") as fh:
                return self._send(200, fh.read(), ctype)

        tpl = ("index.html" if path == "/" else
               "ayuda.html" if path == "/ayuda" else "admin.html")
        html = env.get_template(tpl).render(username="juan", is_admin=False, account_id=7)
        # El stub va antes que el JS de la app: ninguna llamada real sale.
        html = html.replace("</head>", SHOT_JS + "</head>", 1)
        return self._send(200, html, "text/html; charset=utf-8")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
