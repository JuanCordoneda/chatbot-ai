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
     "gender": "male", "quality": "pro", "keyword_mode": False, "prompt_standalone": True, "crm_idventa": "8811",
     "prompt": ("Actuá como seguidores reales de un entrenador de fitness. Comentarios cortos "
                "(máximo 8 palabras), variados, en el idioma del post, con buena onda y algo "
                "concreto de lo que se ve en el video.\n\nNunca: “nice post”, "
                "“great content”, hashtags, links, ni preguntar precios."),
     "ranges": {"comentarios": {"verificados": {"min": 6, "max": 10},
                                "comunes": {"min": 20, "max": 40}},
                "likes": [{"min": 300, "max": 600, "prod_id": "101"}],
                "views": [{"min": 2000, "max": 3500, "prod_id": "201"}]}},
    {"id": 2, "ig_username": "luciaramos", "display_name": "Lucía Ramos", "status": "active",
     "gender": "female", "quality": "standard", "keyword_mode": False, "prompt_standalone": False, "crm_idventa": "8812",
     "prompt": "Comentarios de clientas reales de un estudio de pilates. Cálidos, breves, sin emojis.",
     "ranges": {"comentarios": {"verificados": {"min": 4, "max": 8},
                                "comunes": {"min": 15, "max": 30}},
                "likes": [{"min": 200, "max": 400, "prod_id": "102"}]}},
    {"id": 3, "ig_username": "estudiobora", "display_name": "Estudio Bora", "status": "active",
     "gender": None, "quality": "standard", "keyword_mode": False, "prompt_standalone": True, "crm_idventa": "",
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
    if path.startswith("/api/nombre_red"):
        return {"nombres": {"red": "Instagram"}}
    if path.startswith("/api/demora"):
        return {"demora": "45"}
    if path.startswith("/api/costo_trafico"):
        return {"cantmin": "100", "cantmax": "10000", "costoTrafico": "3.4200"}
    if path.startswith("/api/cantidades_usadas"):
        return {"usadas": []}
    if path.startswith("/api/server_time_ar"):
        return {"ts": "2026-08-06 15:00:00"}
    if path.startswith("/api/procesar"):
        return {"job_id": "demo"}
    if path.startswith("/api/me"):
        return {"username": "juan", "is_admin": False}
    return {}



# ── Flujo del generador (para la guía de órdenes) ───────────────────────────
# El post, los comentarios y el costo son inventados: alcanza para que la UI se
# comporte igual que con un post real.
POST_DEMO = {
    "tipo": "listo",
    "owner_username": "peterjfournier",
    "cliente_asignado": True,
    "client_id": "Peter Fournier",
    "is_video": True,
    "gender": "male",
    "caption": "3 ejercicios para la espalda que podés hacer en casa 💪 Guardalo para no perderlo.",
    "photo_description": "Un entrenador en un gimnasio muestra tres ejercicios de espalda con banda elástica.",
    "transcription": "Hoy te traigo tres ejercicios de espalda que podés hacer en casa con una banda…",
    "ranges": {"likes": [{"min": 300, "max": 600, "prod_id": "101"}],
               "views": [{"min": 2000, "max": 3500, "prod_id": "201"}],
               "comentarios": {"verificados": {"min": 6, "max": 10},
                               "comunes": {"min": 20, "max": 40}}},
}

COMENTARIOS_DEMO = [
    "Justo lo que necesitaba para hoy", "La banda cambia todo, probé y quedé roto",
    "Guardado para la rutina del lunes", "El segundo ejercicio me mata la espalda",
    "Grande Peter, siempre con lo práctico", "¿Cuántas series recomendás?",
    "Lo hice en casa y funciona", "Necesitaba algo sin máquinas, gracias",
    "Se nota la técnica, muy claro", "Me lo mandó mi hermano y no falló",
    "Tres ejercicios y listo, ideal", "Empiezo mañana sin excusas",
    "Qué bueno que no hace falta gimnasio", "Lo probé recién y lo sentí al toque",
    "Mi kinesiólogo me mandó algo parecido", "Buenísimo para los días sin tiempo",
    "La explicación clarísima como siempre", "Voy a sumarlo a la rutina de espalda",
    "Con banda es mucho más llevadero", "Justo venía con dolor de espalda",
    "Me sirve para las mañanas antes del trabajo", "Simple y bien explicado",
    "Ya lo guardé para el finde", "El tercero es el que más me cuesta",
    "Gracias por compartirlo, muy útil", "Ideal para arrancar de a poco",
    "Lo mando al grupo de entrenamiento", "Se puede hacer en cualquier lado",
    "Sin excusas entonces", "Muy claro el paso a paso",
]


def stream_demo():
    """Respuesta del stream de generación: el scrape, los comentarios y el listo."""
    lineas = [{"tipo": "progreso", "mensaje": "Accediendo al post..."},
              {**POST_DEMO, "tipo": "scrape"}]
    for i, txt in enumerate(COMENTARIOS_DEMO):
        lineas.append({"tipo": "comentario", "index": i, "texto": txt})
    lineas.append(POST_DEMO)          # tipo "listo"
    return "".join("data: " + json.dumps(l) + "\n" for l in lineas)


# Marcas que se dibujan encima de cada captura: (selector, texto, lado).
# Se pintan en el navegador, así las coordenadas son exactas y quedan dentro
# del PNG (también se ven al abrir la captura en grande).
ANOTACIONES = {
    "lista":             [("#btn-new-client", "Empezá acá", "izq")],
    "ficha-nueva":       [(".ax-prompt-teaser", "Acá adentro se escribe el prompt", "abajo")],
    "ficha:identidad":   [("#client-ig", "Igual que en Instagram: sin @ ni espacios", "abajo")],
    "ficha:prompt":      [("#client-keyword-field", "Sólo para posts de sorteo", "arriba")],
    "ficha:calidad":     [('.ax-field:has(#client-quality) .ax-opt-b[data-val="pro"]',
                           "Sólo para clientes importantes, no para todos", "arriba")],
    "ficha:comentarios": [(".ax-com-card:has(#com-comunes-min)", "Estos salen en 2 tandas", "arriba")],
    "ficha:trafico":     [("#client-venta", "De acá sale la plata", "arriba")],
    "gen":               [("#ig-link", "Pegá el link del post", "abajo")],
    "gen-link":          [("#ig-link", "Pegá el link del post o del reel", "abajo")],
    "gen-paso1":         [(".tipo-panel--verificado .tp-rnd", "Te pregunta cuántos y los marca solo", "abajo")],
    "gen-paso2":         [("#btn-cartel-saltar", "Si el cliente no lleva comunes, saltá", "abajo")],
    "gen-paso3":         [("#btn-cartel-saltar", "También es opcional", "abajo")],
    "gen-orden":         [("#orden-producto", "Elegí el producto: el costo se calcula solo", "abajo")],
    "gen-cuando":        [('.cuando-pill[data-value="split3"]', "Parte la cantidad en varias órdenes", "abajo")],
    "gen-ordenes":       [("#btn-solicitar", "Recién acá salen al panel", "arriba")],

}

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

// ── Marcas encima de la captura ─────────────────────────────────────────────
// Un aro amarillo sobre el elemento y un cartelito al lado. Se dibujan al final,
// cuando la escena ya está armada: antes las coordenadas serían las de otro
// layout.
const ANOTACIONES = __ANOTACIONES__;
function dibujarMarcas() {
  for (const [sel, texto, lado] of ANOTACIONES) {
    const el = document.querySelector(sel);
    if (!el) { console.warn("anotación sin elemento:", sel); continue; }
    const r = el.getBoundingClientRect();
    // Si el elemento quedó fuera de la captura, el cartel se pegaría contra un
    // borde señalando a la nada: mejor no dibujar nada.
    if (r.bottom > window.innerHeight - 8 || r.top < 8) {
      console.warn("anotación fuera de la captura:", sel); continue;
    }
    // Las coordenadas del rect ya vienen con el zoom de .app aplicado; el
    // overlay va en el body (sin zoom), así que se usan tal cual.
    const aro = document.createElement("div");
    aro.style.cssText = `position:fixed;left:${r.left - 7}px;top:${r.top - 7}px;
      width:${r.width + 14}px;height:${r.height + 14}px;border:3px solid #FAB900;
      border-radius:${Math.min(16, r.height / 2 + 8)}px;box-shadow:0 0 0 4px rgba(250,185,0,.22);
      pointer-events:none;z-index:9999;`;
    document.body.appendChild(aro);

    const cartel = document.createElement("div");
    cartel.textContent = texto;
    cartel.style.cssText = `position:fixed;z-index:9999;background:#181818;color:#FAB900;
      border:2px solid #FAB900;font-family:Inter,system-ui,sans-serif;font-weight:750;
      font-size:15px;line-height:1.25;padding:6px 12px;border-radius:9px;max-width:290px;
      box-shadow:0 8px 24px rgba(0,0,0,.5);`;
    document.body.appendChild(cartel);
    const c = cartel.getBoundingClientRect();
    let x, y;
    if (lado === "izq")        { x = r.left - c.width - 22;  y = r.top + r.height / 2 - c.height / 2; }
    else if (lado === "arriba"){ x = r.left;                 y = r.top - c.height - 16; }
    else                       { x = r.left;                 y = r.bottom + 16; }
    // Que no se vaya de la captura.
    x = Math.max(12, Math.min(x, window.innerWidth - c.width - 12));
    y = Math.max(12, Math.min(y, window.innerHeight - c.height - 12));
    cartel.style.left = x + "px";
    cartel.style.top = y + "px";

  }
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
    // Al re-colgar el nodo, las animaciones de entrada (fundidos) arrancan de
    // cero y la captura sale a medio aparecer: se cortan de raíz.
    el.classList.remove("etapa-entrando");
    el.style.animation = "none";
    el.querySelectorAll("*").forEach(n => { n.style.animation = "none"; n.style.opacity = ""; });
    // El .app del panel escala todo un 20%: sin esa clase el recorte sale más
    // chico que en la pantalla real.
    w.classList.add("app");
  };


  // ── Escenas del generador (guía de órdenes) ──────────────────────────────
  if (s.startsWith("gen-")) {
    const LINK = "https://www.instagram.com/p/DEMO123/";
    const input = document.getElementById("ig-link");
    input.value = LINK;
    input.dispatchEvent(new Event("input"));
    if (s === "gen-link") { dibujarMarcas(); return; }

    generarComentarios();
    // La elección va en 3 pasos (verificados → comunes → WhatsApp). Se marcan
    // unos cuantos de la etapa en curso, igual que haría el vendedor.
    // Con 30 comentarios la captura sale de 3 metros: para la guía alcanza con
    // ver el arranque de la lista.
    const recortarLista = (n) => {
      document.querySelectorAll(".tipo-panel:not(.hidden) .comentario-item").forEach((it, i) => {
        if (i >= n) it.remove();
      });
    };
    const marcarAlgunos = (n) => {
      const cajas = [...document.querySelectorAll(".tipo-panel:not(.hidden) .comentario-item input[type=checkbox]")]
        .filter(c => c.offsetParent !== null);
      cajas.slice(0, n).forEach((c) => { if (!c.checked) c.click(); });
    };
    setTimeout(() => {
      marcarAlgunos(6);

      if (s === "gen-paso1") { recortarLista(9); solo(document.querySelector("#step-comentarios .comments-card")); setTimeout(dibujarMarcas, 80); return; }

      avanzarEtapa();                       // → PASO 2: comunes
      setTimeout(() => {
        // La ficha del cliente premarca su cantidad; para la captura se deja una
        // selección chica y así al paso 3 le sobra lista de verdad.
        deseleccionarTodos();
        marcarAlgunos(8);
        if (s === "gen-paso2") { recortarLista(9); solo(document.querySelector("#step-comentarios .comments-card")); setTimeout(dibujarMarcas, 80); return; }

        avanzarEtapa();                     // → PASO 3: WhatsApp
        setTimeout(() => {
          if (s === "gen-paso3") { recortarLista(9); solo(document.querySelector("#step-comentarios .comments-card")); setTimeout(dibujarMarcas, 80); return; }

          saltarEtapa();                    // → órdenes, sin mandar nada por WhatsApp
          setTimeout(() => {
        if (s === "gen-orden")  { solo(document.querySelector("#step-ordenes .orden-form-card")); setTimeout(dibujarMarcas, 80); return; }
        if (s === "gen-cuando") {
          // "Dividir en 3/5" sólo aparece con un producto de tráfico elegido
          // (en comentarios no tiene sentido partir la cantidad).
          const prod = document.getElementById("orden-producto");
          prod.value = "101";
          onProductoChange();
          setTimeout(() => {
            solo(document.querySelector(".cuando-pills").closest(".orden-field"));
            setTimeout(dibujarMarcas, 60);
          }, 400);
          return;
        }
        if (s === "gen-ordenes") {
          // Las órdenes precreadas con los rangos del cliente, y abajo el botón
          // que las manda: el botón vive fuera de la tarjeta, así que se
          // agrupan a mano para que entren los dos en la captura.
          const caja = document.createElement("div");
          caja.appendChild(document.getElementById("ordenes-acumuladas-card"));
          caja.appendChild(document.querySelector("#step-ordenes .nuevo-post-bar"));
          solo(caja);
          setTimeout(dibujarMarcas, 60); return;
        }
          }, 2200);
        }, 700);
      }, 700);
    }, 1600);
    return;
  }

  if (!s.startsWith("ficha")) { dibujarMarcas(); return; }   // la pantalla entera

  openClientModal(s === "ficha-nueva" ? undefined : 1);
  setTimeout(() => {
    const modal = document.querySelector("#client-mo .ax-modal");
    if (s === "ficha" || s === "ficha-nueva") { modal.style.maxHeight = "none"; solo(modal); setTimeout(dibujarMarcas, 60); return; }
    // El editor del prompt sólo existe a pantalla completa: el modal normal
    // muestra el teaser, no el textarea.
    if (s === "ficha:prompt") {
      togglePromptFull();
      setTimeout(() => { modal.style.height = "620px"; solo(modal); setTimeout(dibujarMarcas, 60); }, 250);
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
    setTimeout(dibujarMarcas, 60);
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

    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path.startswith("/api/stream/"):
            return self._send(200, stream_demo(), "text/event-stream")

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
               "ayuda.html" if path == "/ayuda" else
               "ayuda-ordenes.html" if path == "/ayuda-ordenes" else "admin.html")
        # url_for lo pone Flask; acá alcanza con una versión mínima para que
        # las plantillas que arman URLs absolutas (og:image) rendericen.
        def url_for(endpoint, filename="", _external=False, **kw):
            base = "http://127.0.0.1:8899" if _external else ""
            return f"{base}/static/{filename}" if endpoint == "static" else base + "/"
        html = env.get_template(tpl).render(username="juan", is_admin=False, account_id=7,
                                            url_for=url_for)
        # El stub va antes que el JS de la app: ninguna llamada real sale.
        shot = ""
        for parte in self.path.split("?")[-1].split("&"):
            if parte.startswith("shot="):
                shot = parte[5:]
        anotaciones = json.dumps(ANOTACIONES.get(shot, []))
        html = html.replace("</head>", SHOT_JS.replace("__ANOTACIONES__", anotaciones) + "</head>", 1)
        return self._send(200, html, "text/html; charset=utf-8")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
