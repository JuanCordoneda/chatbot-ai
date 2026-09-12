"""
Dos cosas que pidió Lautaro (Growi) y que viven en el mismo lugar: la ficha del
cliente y cómo se arma cada post.

GUION DE LA TANDA — la variedad de los comentarios la sortea el código, no el
modelo. Lo que se afirma acá:
  - el reparto de largos y tipos respeta las proporciones EXACTAS, no las que
    le salgan al azar en esa tanda;
  - no quedan bloques de más de dos comentarios del mismo largo seguidos;
  - hay una ficha por comentario pedido, y dos tandas no salen iguales;
  - en mixto se dice qué fichas van en cada sección de género, pero SIN meter
    una línea de sección dentro de la lista (el modelo la copiaría y se
    publicaría como comentario);
  - va en el tramo de la tanda (no cacheado), antes del FORMATO DE SALIDA;
  - no entra en modo keyword, ni con un prompt que trae su propio formato, ni
    con CROW_GUION apagado;
  - el piso de oficio ya no impone minúsculas ni "la mayoría cortos".

POST FLOJO — cada tanto un post sale por debajo del rango. Se afirma:
  - la ficha lo guarda normalizado (clamps, apagado con 0) y no pisa el resto;
  - el sorteo sale con la frecuencia configurada y devuelve None sin config.

No llama a la API ni a la base.
"""
import os
import random
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


# ── 1. Reparto exacto y sin bloques ──────────────────────────────────────────
rng = random.Random(7)
largos = ai._repartir(ai._GUION_LARGOS, 80, rng)
cuenta = {et: largos.count(et) for et, _ in ai._GUION_LARGOS}
check("reparto: 80 fichas", len(largos) == 80)
check("reparto: 30/45/25 exacto sobre 80",
      sorted(cuenta.values()) == [20, 24, 36], cuenta)

peor = 0
for semilla in range(200):
    r = random.Random(semilla)
    et = ai._romper_bloques(ai._repartir(ai._GUION_LARGOS, 84, r), r)
    racha = mejor = 1
    for a, b in zip(et, et[1:]):
        racha = racha + 1 if a == b else 1
        mejor = max(mejor, racha)
    peor = max(peor, mejor)
check("sin bloques: nunca más de 2 del mismo largo seguidos (200 tandas)",
      peor <= ai._GUION_MAX_SEGUIDOS, f"racha máxima {peor}")

malos = 0
for semilla in range(200):
    r = random.Random(semilla)
    la = ai._repartir(ai._GUION_LARGOS, 60, r)
    ti = ai._emparejar(la, ai._repartir(ai._GUION_TIPOS, 60, r), r)
    malos += sum(1 for l, t in zip(la, ti)
                 if (ai._es_corto(l) and t in ai._GUION_NO_CORTO)
                 or (not ai._es_corto(l) and t in ai._GUION_SOLO_CORTO))
    check_mismos = sorted(ti) == sorted(ai._repartir(ai._GUION_TIPOS, 60, random.Random(0)))
    if not check_mismos:
        malos += 1000
check("emparejar: ningún corto con anécdota/detalle y ningún largo 'solo emoji'",
      malos == 0, f"{malos} fichas imposibles")

# ── 2. El bloque del guion ───────────────────────────────────────────────────
carrusel = ai._guion_tanda("male", 80, random.Random(3), is_video=False, n_imagenes=5)
check("carrusel: sin enfoques de video", "edición" not in carrusel and "del video" not in carrusel)
check("carrusel: con fotos del carrusel", "foto puntual del carrusel" in carrusel)
video = ai._guion_tanda("male", 80, random.Random(3), is_video=True)
check("video: con enfoques de video", "momento puntual del video" in video)
check("guion: aclara el idioma", "NO define el idioma" in carrusel)

g = ai._guion_tanda(None, 40, random.Random(1))
fichas = [l for l in g.split("---")[1].strip().splitlines() if l.strip()]
check("guion: una ficha por comentario", len(fichas) == 40, len(fichas))
check("guion: numeradas en orden", fichas[0].startswith("1. ") and fichas[-1].startswith("40. "))
check("guion mixto: dice el corte de secciones", 'fichas 1 a 20 van debajo de "mujeres:"' in g)
check("guion mixto: NINGUNA línea de sección dentro de la lista",
      not any("mujeres" in l or "hombres" in l for l in fichas))
check("guion: aclara que el estilo manda", "gana lo de arriba" in g)

g_m = ai._guion_tanda("male", 30, random.Random(1))
check("guion hombres: sin corte de secciones", "mujeres" not in g_m)
check("guion: dos tandas no salen iguales",
      ai._guion_tanda(None, 40, random.Random(1)) != ai._guion_tanda(None, 40, random.Random(2)))

# ── 3. Dónde entra en el prompt ──────────────────────────────────────────────
class _RepoFalso:
    def __init__(self, generico="REGLAS GENERALES."):
        self.generico = generico

    def get_generic_prompt(self):
        return self.generico

    def get_client_prompt_layer(self, ig, account_id=None):
        return "Comentarios del cliente.", True


ai._repo = _RepoFalso()
try:
    p = ai._load_prompt_partes("caption", [], client_id="x", cantidad=36)
    check("prompt: el guion va en la tanda", "GUION DE ESTA TANDA" in p.tanda)
    check("prompt: NO en el template cacheable", "GUION" not in p.template)
    check("prompt: antes del FORMATO DE SALIDA",
          p.tanda.index("GUION DE ESTA TANDA") < p.tanda.index("FORMATO DE SALIDA"))
    check("prompt: tantas fichas como la cantidad pedida", "\n36. " in p.tanda)

    kw = ai._load_prompt_partes("caption", [], client_id="x", keyword="TOOLKIT")
    check("keyword: sin guion", "GUION" not in (kw.template + kw.contexto + kw.tanda))

    ai._repo = _RepoFalso("FORMATO DE SALIDA propio\nhombres:\n...")
    propio = ai._load_prompt_partes("caption", [], client_id=ai.GENERIC_CLIENT_ID)
    check("formato propio: sin guion", "GUION" not in propio.tanda)

    ai._repo = _RepoFalso()
    ai._GUION_ON = False
    apagado = ai._load_prompt_partes("caption", [], client_id="x", cantidad=36)
    check("CROW_GUION=0: sin guion", "GUION" not in apagado.tanda)
finally:
    ai._GUION_ON = True
    ai._repo = None

check("piso: ya no impone minúsculas", "arranquen en minúscula" not in ai._PISO_OFICIO)
check("piso: ya no impone 'la mayoría cortos'", "mayoría cortos" not in ai._PISO_OFICIO)

# ── 4. Post flojo: la ficha ──────────────────────────────────────────────────
try:
    from common import repository as repo
except Exception as e:          # sin sqlalchemy/DB a mano: se saltea esta parte
    repo = None
    print(f"(salteo la ficha: {e})")

if repo is not None:
    n = repo._norm_ranges({"likes": [{"min": 5000, "max": 8000}],
                           "bajon": {"cada": 6, "pct_min": 50, "pct_max": 80}})
    check("ficha: guarda el bajón", n.get("bajon") == {"cada": 6, "pct_min": 50, "pct_max": 80}, n)
    check("ficha: no pisa los rangos", n.get("likes") == [{"min": 5000, "max": 8000}], n)
    check("ficha: cada=0 es apagado", repo._norm_bajon({"cada": 0}) is None)
    check("ficha: basura es apagado", repo._norm_bajon({"cada": "x"}) is None)
    check("ficha: porcentajes invertidos se ordenan",
          repo._norm_bajon({"cada": 5, "pct_min": 90, "pct_max": 40}) == {"cada": 5, "pct_min": 40, "pct_max": 90})
    check("ficha: clamps", repo._norm_bajon({"cada": 1, "pct_min": 1, "pct_max": 400})
          == {"cada": 3, "pct_min": 10, "pct_max": 95})

# ── 5. Post flojo: el sorteo ─────────────────────────────────────────────────
try:
    import langchainService as ls
except Exception as e:
    ls = None
    print(f"(salteo el sorteo: {e})")

if ls is not None:
    random.seed(123)
    cfg = {"bajon": {"cada": 5, "pct_min": 50, "pct_max": 80}}
    tiros = [ls._sortear_bajon(cfg) for _ in range(5000)]
    flojos = sum(1 for t in tiros if t)
    check("sorteo: ~1 de cada 5", 850 < flojos < 1150, flojos)
    check("sorteo: devuelve los porcentajes", next(t for t in tiros if t) == {"pct_min": 50, "pct_max": 80})
    check("sorteo: sin config, nunca", all(ls._sortear_bajon(r) is None
                                           for r in ({}, None, {"likes": []}, {"bajon": {}})))


print()
if FALLOS:
    print(f"{len(FALLOS)} FALLA(S):")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("Todo OK")
