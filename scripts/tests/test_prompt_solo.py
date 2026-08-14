"""
El switch "usar solo este prompt" de la ficha del cliente.

Por defecto el prompt final son dos capas: el genérico (reglas de la agencia)
arriba y el del cliente abajo. Hay cuentas cuyo estilo es lo contrario de esas
reglas, y para esas la ficha tiene un switch que saca la capa de arriba.

Lo que se afirma acá:
  - prendido, el genérico NO aparece en el prompt final (ni su texto ni el
    separador de capas): si se colara, el cliente seguiría peleando con las
    reglas que justamente pidió no tener, y encima pagando esos tokens;
  - apagado, el armado de siempre no cambia — genérico arriba, cliente abajo,
    separador en el medio;
  - prendido pero sin prompt cargado, cae al genérico: mejor eso que mandarle
    al modelo un template vacío;
  - el genérico solo (post sin cliente) sigue saliendo igual.

No llama a la API: se stubea el repositorio, que es de donde salen los dos
prompts en prod.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "openAIService"))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-no-se-usa")

from modules import ai_generator as ai                              # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(f"{'OK  ' if cond else 'FALLA'}  {nombre}")
    if not cond:
        FALLOS.append(f"{nombre} {detalle}".strip())


GENERICO = "REGLAS GENERALES DE LA AGENCIA: variá los largos, no suenes a bot."
CLIENTE = "Comentarios en alemán, secos, sin emojis."


class _RepoFalso:
    """Lo mínimo que ai_generator le pide al repositorio para armar el template."""

    def __init__(self, prompt_cliente, solo, generico=GENERICO):
        self.prompt_cliente, self.solo, self.generico = prompt_cliente, solo, generico

    def get_generic_prompt(self):
        return self.generico

    def get_client_prompt_layer(self, ig_username, account_id=None):
        return (self.prompt_cliente or None), self.solo


def armar(prompt_cliente, solo, client_id="cliente_test", generico=GENERICO):
    ai._repo = _RepoFalso(prompt_cliente, solo, generico)
    try:
        return ai._load_template(client_id, account_id=1)
    finally:
        ai._repo = None


# ── 1. Prendido: va SOLO el prompt del cliente ────────────────────────────────
t = armar(CLIENTE, solo=True)
check("prendido: está el prompt del cliente", CLIENTE in t)
check("prendido: NO está el genérico", GENERICO not in t, repr(t[:200]))
check("prendido: NO está el separador de capas",
      "INSTRUCCIONES ESPECÍFICAS DE ESTE CLIENTE" not in t)
check("prendido: no arrastra nada más", t.strip() == CLIENTE, repr(t))

# ── 2. Apagado: el armado por capas de siempre ───────────────────────────────
t = armar(CLIENTE, solo=False)
check("apagado: está el genérico", GENERICO in t)
check("apagado: está el prompt del cliente", CLIENTE in t)
check("apagado: el genérico va ARRIBA del cliente", t.index(GENERICO) < t.index(CLIENTE))
check("apagado: está el separador de capas",
      "INSTRUCCIONES ESPECÍFICAS DE ESTE CLIENTE" in t)

# ── 3. Prendido pero sin prompt propio: cae al genérico ──────────────────────
t = armar("", solo=True)
check("prendido sin prompt: cae al genérico", t.strip() == GENERICO.strip(), repr(t[:200]))
check("prendido sin prompt: no queda vacío", bool(t.strip()))

# ── 4. Post sin cliente: el genérico solo, como siempre ──────────────────────
t = armar(CLIENTE, solo=True, client_id=ai.GENERIC_CLIENT_ID)
check("cliente genérico: sale el genérico solo", t.strip() == GENERICO.strip())

t = armar(CLIENTE, solo=True, client_id=None)
check("sin cliente: sale el genérico solo", t.strip() == GENERICO.strip())


print()
if FALLOS:
    print(f"{len(FALLOS)} FALLA(S):")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("Todo OK")
