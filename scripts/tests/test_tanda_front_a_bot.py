"""
Lo que arma el front es exactamente lo que el bot sabe cortar.

El botón "Mandarle los N al bot" abre el chat del bot con un bloque ya escrito,
y el bot lo parte en un mensaje por comentario. Son dos programas distintos, en
dos lenguajes distintos, que se pusieron de acuerdo en un formato de texto: si
uno de los dos cambia el armado, el otro deja de reconocerlo y el pedido cae al
modelo, que contesta cualquier cosa en vez de repartir. No hay error, no hay
log: simplemente deja de funcionar.

Por eso este test cruza el río: arma el texto con el JS DE VERDAD (no una copia
pegada acá) y lo parsea con el parser DE VERDAD del bot.

Lo que se afirma:
  - el bloque del front dispara el reparto (la primera línea "Comentarios");
  - el link del post se reconoce como link y no como un comentario más;
  - los comentarios destildados no viajan;
  - un comentario con saltos de línea llega en UNA sola línea — si no, el bot lo
    partiría en dos mensajes y se publicaría medio comentario en el post;
  - los emoji sobreviven el viaje;
  - un comentario SUELTO (el botón de cada fila) NO se confunde con una tanda.
"""
import json
import os
import pathlib
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "whatsappService"))

import app as wa                                                    # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(f"{'OK  ' if cond else 'FALLA'}  {nombre}")
    if not cond:
        FALLOS.append(f"{nombre} {detalle}".strip())


def texto_del_front(items, url):
    """Corre _textoTodoEnUno() del app.js real y devuelve lo que arma.

    Se extrae la función del archivo en vez de reimplementarla: una copia acá
    dejaría de reflejar el front en cuanto alguien lo tocara, que es justo lo
    que este test tiene que detectar.
    """
    js = (RAIZ / "webService/static/app.js").read_text()
    ini = js.index("function _textoTodoEnUno() {")
    fin = js.index("function mandarTodoEnUno() {")
    harness = f"""
      let currentUrl = {json.dumps(url)};
      let repartoItems = {json.dumps(items)};
      {js[ini:fin]}
      process.stdout.write(_textoTodoEnUno());
    """
    r = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"el JS no corrió: {r.stderr[:400]}")
    return r.stdout


URL = "https://www.instagram.com/p/ABC123/"
ITEMS = [
    {"tipo": "link", "texto": f"Comentarios\n{URL}", "incluido": True},
    {"tipo": "comentario", "texto": "qué genia 🔥", "incluido": True},
    {"tipo": "comentario", "texto": "me encanta\nel look", "incluido": True},
    {"tipo": "comentario", "texto": "este no va", "incluido": False},
    {"tipo": "comentario", "texto": "tremenda foto", "incluido": True},
]

bloque = texto_del_front(ITEMS, URL)
pedido = wa._parsear_pedido(bloque)

check("el bloque del front dispara el reparto", pedido is not None, repr(bloque[:60]))

if pedido:
    url, coms = pedido
    check("el link del post se reconoce como link", url == URL, url)
    check("llegan solo los comentarios tildados",
          coms == ["qué genia 🔥", "me encanta el look", "tremenda foto"], coms)
    check("el comentario multilínea viaja en UNA línea",
          all("\n" not in c for c in coms), coms)
    check("los emoji sobreviven", "🔥" in coms[0], coms[0])
    check("el destildado no viaja", "este no va" not in coms)
    check("salen link + un mensaje por comentario", 1 + len(coms) == 4, len(coms))

# El botón de cada fila manda UN comentario suelto: eso es charla, no una tanda.
# Si el bot lo tomara como pedido, el vendedor recibiría un eco en vez de poder
# reenviarlo.
check("un comentario suelto no se confunde con una tanda",
      wa._parsear_pedido("qué genia 🔥") is None)

# Y el tope del bot tiene que seguir alcanzando para una tanda normal.
check("una tanda típica entra en el máximo del bot",
      wa.MAX_COMENTARIOS >= 20, wa.MAX_COMENTARIOS)

print()
if FALLOS:
    print(f"{len(FALLOS)} FALLA(S):")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("Todo en orden.")
