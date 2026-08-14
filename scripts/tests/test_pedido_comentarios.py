"""
El vendedor le pide los comentarios al bot por WhatsApp y los recibe sueltos.

Manda UN mensaje con todo pegado ("enviame estos comentarios" + el link + los
comentarios, uno por línea) y el bot le devuelve cada uno en su propio mensaje,
para reenviarlos al grupo con la selección múltiple de WhatsApp.

Lo que se afirma acá:
  - la tanda que sale es EXACTAMENTE la del botón de la app ("Comentarios" con
    el link primero, después cada comentario pelado): si los dos caminos no
    mandan lo mismo, el grupo recibe dos formatos según por dónde se pidió;
  - la numeración con la que vienen pegados se saca, porque el comentario se
    reenvía tal cual y el "1." terminaría comentado adentro del post;
  - una charla normal con el bot NO se confunde con un pedido y sigue yendo al
    modelo;
  - el reintento del webhook de Meta no reparte la tanda dos veces (la tarda
    más de lo que Meta espera, así que el reintento es lo normal, no el borde);
  - una tanda más grande que el máximo avisa en vez de arriesgar el número.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "whatsappService"))

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido
_rq.post = _prohibido
_rq.get = _prohibido

import app as wa                                                    # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(f"{'OK  ' if cond else 'FALLA'}  {nombre}")
    if not cond:
        FALLOS.append(f"{nombre} {detalle}".strip())


# El hilo del reparto corre acá mismo: sin esto habría que dormir y adivinar
# cuándo terminó, y el test pasaría o fallaría según la máquina.
class _HiloSincronico:
    def __init__(self, target=None, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


wa.threading.Thread = _HiloSincronico
wa.SEND_BULK_DELAY = 0

# En prod la bandera va apagada. Se prende acá porque lo que se está probando es
# la función; que apagada no haga nada se verifica al final, aparte.
wa.PEDIDO_ACTIVO = True

MANDADOS = []
FALLA_DESDE = None            # índice a partir del cual _enviar_texto falla


def _enviar_falso(numero, texto):
    MANDADOS.append((numero, texto))
    if FALLA_DESDE is not None and len(MANDADOS) > FALLA_DESDE:
        return False, "Ese número no tiene WhatsApp o no puede recibir mensajes."
    return True, ""


wa._enviar_texto = _enviar_falso

URL = "https://www.instagram.com/p/ABC123/"
NUM = "54223153407778"


def _pedir(texto, mid):
    del MANDADOS[:]
    tomado = wa._atender_pedido(NUM, texto, mid)
    return tomado, [t for _, t in MANDADOS]


# ── El pedido completo ───────────────────────────────────────────────────────
tomado, salida = _pedir(
    f"enviame estos comentarios\n{URL}\n"
    "1. qué genia\n"
    "2) me encanta el look\n"
    "- tremenda foto\n"
    "• se pasó\n",
    "wamid.1")

check("el pedido lo toma el reparto y no el modelo", tomado is True)
check("primero va 'Comentarios' con el link",
      salida[:1] == [f"Comentarios\n{URL}"], salida[:1])
check("cada comentario llega suelto y sin numeración",
      salida[1:] == ["qué genia", "me encanta el look", "tremenda foto", "se pasó"],
      salida[1:])
check("todo va al número que pidió",
      all(n == NUM for n, _ in MANDADOS))

# ── Mismo formato que el botón de la app ─────────────────────────────────────
# La app arma ([f"Comentarios\n{url}"] if url else []) + comentarios. Si esto
# se despega, el grupo recibe dos formatos distintos.
comentarios = ["qué genia", "me encanta el look"]
esperado_app = [f"Comentarios\n{URL}"] + comentarios
_, salida = _pedir(f"comentarios\n{URL}\n" + "\n".join(comentarios), "wamid.2")
check("la tanda es la misma que manda el botón de la app",
      salida == esperado_app, salida)

# ── Sin link ─────────────────────────────────────────────────────────────────
_, salida = _pedir("mandame los comentarios\nqué genia\nse pasó", "wamid.3")
check("sin link no se inventa un mensaje de link",
      salida == ["qué genia", "se pasó"], salida)

# ── Lo que NO es un pedido ───────────────────────────────────────────────────
check("una sola línea sobre comentarios sigue siendo charla",
      wa._parsear_pedido("cuántos comentarios me quedan?") is None)
check("un mensaje multilínea sin el disparador sigue siendo charla",
      wa._parsear_pedido("hola\ncómo va?\ntodo bien?") is None)
check("un mensaje vacío no es un pedido", wa._parsear_pedido("") is None)

# ── Escrito como salga ───────────────────────────────────────────────────────
check("mayúsculas y acentos no rompen el disparador",
      wa._parsear_pedido("ENVIAME estos COMENTARIOS\nqué genia") is not None)
check("sin acentos tampoco",
      wa._parsear_pedido("mandame los comentarios\nqué genia") is not None)

# ── El reintento de Meta ─────────────────────────────────────────────────────
texto = f"comentarios\n{URL}\nqué genia\nse pasó"
_, primera = _pedir(texto, "wamid.repetido")
tomado, segunda = _pedir(texto, "wamid.repetido")
check("el reintento del webhook no reparte de nuevo",
      len(primera) == 3 and segunda == [], f"1ra={len(primera)} 2da={segunda}")
check("el reintento igual se da por atendido (no cae al modelo)", tomado is True)

# ── Los bordes ───────────────────────────────────────────────────────────────
_, salida = _pedir("enviame estos comentarios\n" +
                   "\n".join(f"comentario {i}" for i in range(wa.MAX_COMENTARIOS + 1)),
                   "wamid.muchos")
check("una tanda pasada de largo avisa y no manda nada",
      len(salida) == 1 and "máximo" in salida[0], salida)

_, salida = _pedir(f"enviame estos comentarios\n{URL}", "wamid.solo-link")
check("un pedido sin comentarios explica qué falta",
      len(salida) == 1 and "línea" in salida[0], salida)

# ── Cuando Meta rechaza a mitad de camino ────────────────────────────────────
FALLA_DESDE = 2
_, salida = _pedir(f"comentarios\n{URL}\nuno\ndos\ntres", "wamid.fallo")
check("si falla parte de la tanda, se avisa por el mismo chat",
      salida[-1].startswith("Te mandé 2 de 4"), salida[-1])
FALLA_DESDE = None

# ── Con la bandera apagada, el bot es el de siempre ──────────────────────────
# Es como sale a prod: el código está, pero no se usa. Lo que importa no es solo
# que no reparta, sino que devuelva False, porque eso es lo que deja al mensaje
# seguir hacia el modelo. Si devolviera True, el pedido moriría en silencio.
wa.PEDIDO_ACTIVO = False
tomado, salida = _pedir(f"enviame estos comentarios\n{URL}\nqué genia", "wamid.apagado")
check("apagada: no manda nada", salida == [], salida)
check("apagada: el mensaje sigue al modelo, como antes", tomado is False)
wa.PEDIDO_ACTIVO = True

print()
if FALLOS:
    print(f"{len(FALLOS)} FALLA(S):")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("Todo en orden.")
