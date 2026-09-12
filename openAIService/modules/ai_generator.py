import os
import random
import re
import time
import unicodedata
import anthropic
from pathlib import Path

# Quién decide qué es un encabezado de género lo sabe common/ordenes: es el
# mismo criterio con el que después se arman las órdenes del CRM. Solo usa
# `random`, no arrastra la capa de datos (por eso no va en el try de más abajo).
from common.ordenes import es_header_genero
from common import idioma as _idioma

# max_retries alto: el SDK reintenta solo los 429/529 (overloaded) al abrir el stream
_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=4)
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Calidad del motor por cliente. Cada cliente tiene asignado "pro" o "standard"
# desde el admin y de ahí sale QUÉ modelo genera sus comentarios: el pro es más
# caro y más fino, el standard alcanza para la mayoría de las cuentas.
#
# Van por env var a propósito: subir de familia de modelo es cambiar una
# variable y reiniciar, no tocar código y redeployar.
_MODEL_PRO = os.environ.get("CROW_MODEL_PRO", "claude-opus-4-8")
_MODEL_STANDARD = os.environ.get("CROW_MODEL_STANDARD", "claude-sonnet-5")

# La descripción de la foto NO usa el modelo del cliente: describir en 5
# oraciones lo que se ve en una imagen no mejora con el modelo caro, y es una
# llamada por post. Va siempre en el liviano.
_MODEL_VISION = os.environ.get("CROW_MODEL_VISION", _MODEL_STANDARD)

# Idioma del contenido que producimos sobre el post (descripción visual y
# transcripción). La definición vive en common/idioma.py: la comparte
# post_processor, que es quien transcribe.
IDIOMA_CONTENIDO_NOMBRE = _idioma.NOMBRE
_ETIQUETA_CONJUNTO = _idioma.ETIQUETA_CONJUNTO


# ── Contabilidad de tokens ────────────────────────────────────────────────────
#
# Precio por MILLÓN de tokens (USD): (entrada, salida). El costo se calcula y se
# guarda en el momento de la llamada, así un cambio de tarifa no reescribe la
# historia de lo que ya se gastó.
#
# OJO con sonnet-5: tiene precio introductorio de 2/10 hasta el 31/08/2026, y
# después pasa a 3/15. Por eso las tarifas son overridables por env var: cuando
# cambie, se toca la variable y listo, sin redeploy.
_PRECIOS = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def _tarifa(model: str) -> tuple[float, float]:
    """(precio_entrada, precio_salida) por millón de tokens para este modelo.
    Un modelo desconocido devuelve (0, 0): preferimos un costo en 0 —evidente al
    mirar el reporte— antes que inventar una tarifa que no es."""
    env = os.environ.get(f"CROW_PRECIO_{model.replace('-', '_').upper()}", "").strip()
    if env:
        try:
            entrada, salida = (float(x) for x in env.split("/", 1))
            return entrada, salida
        except ValueError:
            print(f"[tokens] CROW_PRECIO_* inválido para {model}: {env!r}", flush=True)
    base = _PRECIOS.get(model)
    if base is None:
        # Los IDs pueden venir con sufijo de fecha (claude-haiku-4-5-20251001).
        for k, v in _PRECIOS.items():
            if model.startswith(k):
                return v
        print(f"[tokens] sin tarifa para el modelo {model!r}: costo queda en 0", flush=True)
        return (0.0, 0.0)
    return base


def _registrar_uso(kind: str, model: str, usage, *, intento: int = 1,
                   client_id=None, shortcode: str = "",
                   account_id=None, user_id=None) -> None:
    """Loguea el consumo de una llamada: por consola siempre, en la DB si se puede.

    La línea de consola es la que sirve el primer día (sin migrar nada) y la que
    queda si la DB no está; la fila en token_usage es la que permite después
    agrupar por cliente y por día.
    """
    if usage is None:
        return
    try:
        entrada = int(getattr(usage, "input_tokens", 0) or 0)
        salida = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        p_in, p_out = _tarifa(model)
        # Los cacheados se facturan aparte: lectura ~0.1x, escritura ~1.25x.
        costo = ((entrada + cache_read * 0.1 + cache_write * 1.25) / 1e6 * p_in
                 + salida / 1e6 * p_out)
        print(f"[tokens] {kind} modelo={model} intento={intento} "
              f"in={entrada} out={salida} cache_r={cache_read} cache_w={cache_write} "
              f"costo=${costo:.4f}", flush=True)
    except Exception as e:
        print(f"[tokens] no se pudo leer el usage: {e}", flush=True)
        return

    if _repo is None:
        return
    try:
        _repo.registrar_tokens(
            kind=kind, model=model, intento=intento,
            input_tokens=entrada, output_tokens=salida,
            cache_read_tokens=cache_read, cache_creation_tokens=cache_write,
            costo_usd=costo,
            client_ig_username=(client_id or None), shortcode=(shortcode or None),
            # Sin esto la fila queda sin dueño y el panel de gasto por vendedor
            # tiene que adivinar la cuenta a partir del @cliente.
            account_id=account_id, user_id=user_id,
        )
    except Exception as e:
        print(f"[tokens] no se pudo registrar en DB: {e}", flush=True)


def _modelo(quality) -> str:
    """Modelo de la tanda según la calidad asignada al cliente. Cualquier cosa
    que no sea 'pro' (vacío, cliente sin calidad cargada, valor viejo) cae en el
    standard: el modelo barato es el default seguro."""
    return _MODEL_PRO if (quality or "").strip().lower() == "pro" else _MODEL_STANDARD

# Capa de datos multi-tenant opcional: si no está, se usan los .txt de siempre.
try:
    from common import repository as _repo
except Exception:
    _repo = None


# Cliente reservado para los posts que NO son de ningún cliente cargado. Antes
# esos posts usaban el prompt de Peter Fournier, que arrastra sus personajes,
# sus inside jokes y sus @menciones a un post que no tiene nada que ver. Este
# "cliente" tiene el mismo calibre pero sin nada personal. Es del sistema: uno
# solo para todas las cuentas, invisible para los vendedores, y solo el admin le
# edita el prompt desde el panel.
GENERIC_CLIENT_ID = "__generico__"


# MODO KEYWORD ────────────────────────────────────────────────────────────────
# Otra herramienta, no otro cliente: el vendedor escribe una palabra ("CLAUDE",
# "PROMPTS") y salen N comentarios que son SOLO esa palabra, variando
# mayúsculas/minúsculas, como la gente que comenta una keyword para que el bot
# del creador le mande un PDF.
#
# No pasa por las capas de prompt de siempre: el genérico (largos variados,
# emojis, slang, "comentá el post en sí") es exactamente lo contrario de lo que
# se pide acá, y dejarlo arriba solo lo contamina. Tampoco se le manda el
# contexto del post: para escribir una palabra no hace falta ni el caption, ni
# la transcripción, ni la imagen.
#
# El prompt vive en la DB como cliente reservado del sistema (editable desde
# /admin, igual que el genérico) con el .txt de la imagen como fallback.
KEYWORD_CLIENT_ID = "__keyword__"

# Cuántas líneas le pedimos al modelo. No es el total de la tanda: de acá salen
# las FORMAS de escritura distintas (TOOLKIT / Toolkit / toolkit...), que después
# se repiten. Por env var y no hardcodeada: cambiarlo es tocar una variable y
# reiniciar, no redeployar.
KEYWORD_CANTIDAD = int(os.environ.get("CROW_KEYWORD_CANTIDAD", "15"))

# Veces que se repite CADA forma de escritura. Con 4 formas y 15 repeticiones la
# tanda son 60 comentarios: es la cantidad que se publica, no un tope.
KEYWORD_REPETICIONES = int(os.environ.get("CROW_KEYWORD_REPETICIONES", "15"))


# El prompt final se arma en DOS CAPAS:
#
#   1. BASE   = el prompt genérico. Son las reglas de oficio que valen para
#              TODOS los clientes (distribución de largos, mayúsculas/minúsculas,
#              emojis, cómo no sonar a bot). Se edita en un solo lugar y el
#              arreglo le llega a todos los clientes al instante.
#   2. CLIENTE = el prompt del cliente. Solo lo PROPIO de esa cuenta: rubro,
#              personajes, @menciones permitidas, tono, idioma.
#
# Antes cada prompt de cliente era autónomo, así que un arreglo global (ej:
# "que algunos comentarios arranquen en mayúscula") había que copiarlo a mano en
# cada cliente. Con las capas se escribe una vez en el genérico.
#
# La capa del cliente va ABAJO y manda: si se contradicen, gana lo específico.
#
# Excepción: la ficha del cliente tiene un switch ("usa solo su prompt") que
# saca la capa genérica entera. Es para las cuentas cuyo estilo es lo contrario
# de las reglas generales, donde la única salida era escribir "ignorá todo lo
# anterior" y pagar igual los tokens del genérico en cada tanda.
_LAYER_SEP = (
    "\n\n"
    "════════════════════════════════════════════════════════════════\n"
    "INSTRUCCIONES ESPECÍFICAS DE ESTE CLIENTE\n"
    "Todo lo de arriba son las reglas generales de la agencia. Lo que sigue es\n"
    "lo propio de este cliente y TIENE PRIORIDAD: si algo se contradice con las\n"
    "reglas generales, mandá con lo de acá abajo.\n"
    "════════════════════════════════════════════════════════════════\n\n"
)


def _generic_base() -> str:
    """La capa base (prompt genérico): DB primero, si no el archivo de la imagen.
    El genérico es UNO SOLO global: el mismo para todas las cuentas (por eso no
    se filtra por account_id)."""
    if _repo is not None:
        try:
            db_prompt = _repo.get_generic_prompt()
            if db_prompt:
                return db_prompt
        except Exception as e:
            print(f"[ai] prompt genérico de DB no disponible, uso archivo ({e})", flush=True)
    generico = _PROMPTS_DIR / "generico.txt"
    if generico.exists():
        return generico.read_text(encoding="utf-8")
    return ""


def _keyword_base() -> str:
    """El prompt maestro del modo keyword: DB primero, si no el archivo de la
    imagen. Es UNO SOLO global, como el genérico."""
    if _repo is not None:
        try:
            db_prompt = _repo.get_keyword_prompt()
            if db_prompt:
                return db_prompt
        except Exception as e:
            print(f"[ai] prompt keyword de DB no disponible, uso archivo ({e})", flush=True)
    archivo = _PROMPTS_DIR / "keyword.txt"
    if archivo.exists():
        return archivo.read_text(encoding="utf-8")
    return ""


def _keyword_prompt(keyword: str, evitar: list[str] | None = None) -> str:
    """Prompt completo del modo keyword. Autónomo: no lleva capa genérica, capa
    de cliente ni contexto del post."""
    base = _keyword_base()
    keyword = keyword.strip()

    # Igual que con {caption}: reemplazo dirigido, no .format, así una llave
    # suelta que escriba el admin en el prompt no rompe nada. Si el prompt no
    # trae el marcador, la palabra se agrega al final.
    prompt = base.replace("{keyword}", keyword).replace("{cantidad}", str(KEYWORD_CANTIDAD))
    if "{keyword}" not in base:
        prompt += f"\n\nPalabra clave de esta tanda: {keyword}"

    # "Cargar más" en modo keyword: no sirve pedir comentarios "distintos"
    # (son todos la misma palabra), pero sí evitar repetir las MISMAS formas de
    # escritura que ya salieron.
    if evitar:
        formas = sorted({c.strip() for c in evitar if c.strip()})
        if formas:
            prompt += (
                "\n\nESTA ES UNA TANDA ADICIONAL. Estas formas de escritura ya se "
                "usaron: " + " / ".join(formas) + ". Repartí las de esta tanda de "
                "otra manera (otra proporción entre mayúsculas, título y minúsculas), "
                "sin que se note el corte entre una tanda y la otra."
            )

    prompt += (
        "\n\nFORMATO DE SALIDA (obligatorio):\n"
        f"- Devolvé EXACTAMENTE {KEYWORD_CANTIDAD} comentarios, ni uno más ni uno menos.\n"
        "- Uno por línea, sin líneas en blanco entre medio.\n"
        "- Sin numeración, sin viñetas, sin guiones, sin comillas, sin encabezados.\n"
        "- Sin punto final.\n"
        "- No expliques nada ni pidas confirmación: devolvé solo los comentarios."
    )
    return prompt


def _client_layer(client_id: str, account_id: int | None) -> tuple[str, bool]:
    """La capa propia del cliente: DB primero, si no el .txt de la imagen.

    Devuelve (texto, solo). `solo` es la ficha con "usa solo su prompt" prendido:
    ese cliente no lleva la capa genérica arriba. Los .txt de la imagen no tienen
    dónde guardar el flag, así que ese camino siempre arma por capas (que es lo
    que hacían antes de que existiera el switch)."""
    if _repo is not None:
        try:
            db_prompt, solo = _repo.get_client_prompt_layer(client_id, account_id)
            if db_prompt:
                return db_prompt, solo
        except Exception as e:
            print(f"[ai] prompt DB no disponible, uso archivo ({e})", flush=True)
    client_key = client_id.lower().replace(" ", "")
    client_prompt = _PROMPTS_DIR / "clients" / f"{client_key}.txt"
    if client_prompt.exists():
        return client_prompt.read_text(encoding="utf-8"), False
    return "", False


# PISO DE OFICIO (solo para los clientes que van sin capa genérica) ───────────
#
# El genérico son ~7.000 caracteres, y de esos la mitad no son "reglas de la
# agencia" sino ARTESANÍA: qué hace que un comentario parezca escrito por una
# persona y no por una IA. Al sacar la capa entera se iba también eso, y los
# clientes en modo "solo este prompt" empezaron a devolver comentarios planos,
# intercambiables, de los que sirven para cualquier post.
#
# Esto es el piso: lo mínimo de oficio, sin nada de estilo (ni idioma, ni tono,
# ni temas, ni personajes: eso es del cliente). Va abajo del prompt del cliente
# y cede ante él en todo lo que se contradiga — es un piso, no un techo.
#
# Se puede apagar con CROW_PISO_OFICIO=0 para un cliente que necesite control
# absoluto del prompt (ojo: vuelve el problema de los comentarios de bot).
_PISO_ON = os.environ.get("CROW_PISO_OFICIO", "1").strip().lower() not in ("0", "false", "no")

_PISO_OFICIO = (
    "\n\n"
    "════════════════════════════════════════════════════════════════\n"
    "PISO DE OFICIO (lo pone la herramienta, no el cliente)\n"
    "Son reglas de artesanía, no de estilo: si algo de acá choca con las\n"
    "instrucciones de arriba (idioma, tono, largos, temas, personajes), MANDAN\n"
    "LAS DE ARRIBA. Esto es el piso para que no parezca escrito por una IA.\n"
    "════════════════════════════════════════════════════════════════\n"
    "- Cada comentario es de una persona DISTINTA. Nunca variaciones del mismo "
    "mensaje ni el mismo elogio reescrito de diez maneras.\n"
    "- Reaccioná a algo CONCRETO de este post (un gesto, una frase, la edición, "
    "la ropa, el lugar, algo del fondo). Si un detalle no está en el post, no "
    "existe: no lo inventes.\n"
    "- Repartí los comentarios entre 5 y 10 detalles distintos del post, no "
    "todos alrededor de la misma idea.\n"
    "- PROHIBIDO el repertorio de IA: \"great content\", \"love this\", "
    "\"amazing\", \"so true\", \"thanks for sharing\", \"nice post\" y cualquier "
    "elogio intercambiable que serviría igual para otro post.\n"
    # Ni largos ni mayúsculas concretos: son ESTILO. "La mayoría cortos" y
    # "varios en minúscula" chocaban de frente con clientes que piden 45% de
    # medianos y todo con mayúscula, y un prompt que se contradice sale en
    # promedio — que es justo el patrón que se nota.
    "- Largos mezclados y sin patrón visible, desde 1 palabra hasta varias "
    "oraciones.\n"
    "- Escritos desde el teléfono, no redactados: gramática relajada y sin punto "
    "final en los cortos. Mayúsculas/minúsculas: lo que digan las instrucciones "
    "de arriba; si no dicen nada, mezclá.\n"
    "- NO todos positivos: sumá escépticos, confundidos, neutrales, algún "
    "pícaro, y preguntas de verdad.\n"
    "- Ingenio: remates inesperados y específicos de ESTE post. Un comentario "
    "que podría ir en cualquier video no sirve — reescribilo.\n"
    "- No repitas estructuras, inicios de frase, remates ni el mismo emoji a lo "
    "largo de la tanda.\n"
    "- Nunca inventes un @handle.\n"
    "- Antes de dar por bueno cada comentario: ¿parece que vio el post?, "
    "¿parece de otra persona?, ¿podría existir de verdad? Si alguna es \"no\", "
    "reescribilo."
)


def _piso_oficio() -> str:
    return _PISO_OFICIO if _PISO_ON else ""


def _load_template(client_id: str | None, account_id: int | None = None) -> str:
    """Arma el prompt final: base genérica + capa del cliente (ver arriba).

    Sin cliente (o cliente genérico) va solo la base. Si el cliente no tiene
    prompt propio, también va solo la base: es mejor default que el default.txt,
    que quedó como último recurso por si no hay genérico cargado.

    Con "usa solo su prompt" prendido en la ficha, el genérico no se arma: va la
    capa del cliente + el piso de oficio (ver arriba). Si además está sin prompt
    cargado, igual cae a la base — es preferible eso a mandar un template
    vacío."""
    if not client_id or client_id == GENERIC_CLIENT_ID:
        return _generic_base() or (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")

    especifico_raw, solo = _client_layer(client_id, account_id)
    especifico = (especifico_raw or "").strip()
    if especifico and solo:
        return especifico + _piso_oficio()

    base = _generic_base()
    if not especifico:
        return base or (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")
    if not base.strip():
        return especifico
    return base.rstrip() + _LAYER_SEP + especifico


# ── Formato de salida controlado por el SISTEMA (no editable por el usuario) ──
# El género de los comentarios lo define el campo Género del cliente (TAREA 4):
# male -> solo "hombres:", female -> solo "mujeres:", mixto/None -> ambos.
_OUT_BASE_NO = ("- Sin numeración, sin guiones, sin comillas y sin títulos de categoría. "
                "Podés usar @menciones SOLO con cuentas que estén explícitamente "
                "permitidas en las instrucciones de arriba (o el dueño del post si "
                "figura ahí): NUNCA inventes un @handle ni menciones cuentas al azar. "
                "No expliques nada ni pidas confirmación: devolvé solo el/los "
                "encabezado(s) indicado(s) y los comentarios, uno por línea.")


# Cuántos comentarios se piden cuando el llamador no dice nada. Es el número que
# estuvo hardcodeado siempre; hoy el front puede pasar el objetivo real de la
# ficha del cliente (verificados + comunes) y entonces este default no se usa.
_COMENTARIOS_DEFAULT = int(os.environ.get("CROW_COMENTARIOS_DEFAULT", "70"))


def _system_output_format(client_gender, cantidad: int = 0) -> str:
    n = cantidad if cantidad and cantidad > 0 else _COMENTARIOS_DEFAULT
    if client_gender == "male":
        return (
            "\n\nFORMATO DE SALIDA (obligatorio):\n"
            f"- Generá ~{n} comentarios en total, uno por línea, listos para publicar.\n"
            "- La primera línea debe ser EXACTAMENTE:\nhombres:\n"
            "- Debajo, todos los comentarios (todos escritos por hombres).\n"
            "- No incluyas el encabezado \"mujeres:\".\n" + _OUT_BASE_NO
        )
    if client_gender == "female":
        return (
            "\n\nFORMATO DE SALIDA (obligatorio):\n"
            f"- Generá ~{n} comentarios en total, uno por línea, listos para publicar.\n"
            "- La primera línea debe ser EXACTAMENTE:\nmujeres:\n"
            "- Debajo, todos los comentarios (todos escritos por mujeres).\n"
            "- No incluyas el encabezado \"hombres:\".\n" + _OUT_BASE_NO
        )
    # mixto (None u otro)
    return (
        "\n\nFORMATO DE SALIDA (obligatorio):\n"
        f"- Generá ~{n} comentarios en total, completamente mezclados dentro de cada sección.\n"
        "- Primero la línea EXACTAMENTE:\nmujeres:\n"
        "seguida de ~la mitad de los comentarios, escritos por mujeres, uno por línea.\n"
        "- Después la línea EXACTAMENTE:\nhombres:\n"
        "seguida de la otra mitad, escritos por hombres, uno por línea.\n"
        "- Los encabezados \"mujeres:\" y \"hombres:\" van tal cual, en su propia línea.\n" + _OUT_BASE_NO
    )


# ── Guion de la tanda ─────────────────────────────────────────────────────────
#
# Pedirle a un modelo "variá largos, tonos y estructuras de forma irregular" en
# 80 comentarios de una sola vez NO funciona: los modelos no saben ser al azar.
# Lo que sale es un ritmo (corto, medio, largo, pregunta, corto...), los mismos
# arranques y la misma voz disfrazada, y al leer 20 seguidos se nota que los
# escribió uno solo. Es la queja de siempre, con el prompt que sea.
#
# Así que el azar lo pone el CÓDIGO: antes de llamar se sortea una ficha por
# comentario (largo, tipo, a qué reacciona, quién lo escribe) y el modelo
# escribe cada uno según su ficha. La variedad deja de depender de que el modelo
# "se acuerde" de variar, y el reparto cambia de verdad entre posts.
#
# Solo ESTRUCTURA, nunca estilo: idioma, tono, mayúsculas, emojis, temas y
# personajes siguen siendo del prompt, que manda si algo choca. Se apaga con
# CROW_GUION=0.
_GUION_ON = os.environ.get("CROW_GUION", "1").strip().lower() not in ("0", "false", "no")

# (etiqueta, peso). Los pesos no tienen que sumar 100: son proporciones.
_GUION_LARGOS = [
    ("corto (1 a 4 palabras)", 30),
    ("medio (5 a 14 palabras)", 45),
    ("largo (15 a 35 palabras)", 25),
]
_GUION_TIPOS = [
    ("reacción espontánea", 20),
    ("observación de un detalle concreto", 20),
    ("opinión propia, con postura", 14),
    ("pregunta genuina", 10),
    ("experiencia personal relacionada", 10),
    ("humor o ironía", 10),
    ("duda, matiz o leve desacuerdo", 7),
    ("le habla directo al creador", 6),
    ("solo emoji (si el estilo no usa emojis: 1 o 2 palabras)", 3),
]
# Un corto no puede ser una anécdota ni una observación con detalle: en la
# prueba salían "Gris" o "Notebook brand", que no son un comentario sino el
# nombre de una cosa. Y un largo no puede ser "solo emoji".
_GUION_NO_CORTO = {"observación de un detalle concreto", "experiencia personal relacionada",
                   "duda, matiz o leve desacuerdo"}
_GUION_SOLO_CORTO = {"solo emoji (si el estilo no usa emojis: 1 o 2 palabras)"}

_GUION_ENFOQUES_BASE = [
    ("algo que se VE (persona, gesto, ropa, lugar, objeto)", 24),
    ("algo que se dice o se lee (caption, texto en pantalla, audio)", 18),
    ("la idea central del post", 16),
    ("un tema secundario o un detalle que pocos notarían", 14),
    ("lo que le pasa a quien comenta con eso", 12),
]
# Lo que depende del formato: en la prueba, un carrusel de fotos terminó con
# "¿la edición tiene música?" porque el enfoque de edición era de video.
_GUION_ENFOQUES_VIDEO = [("cómo está hecho (edición, ritmo, música)", 8),
                         ("un momento puntual del video", 8)]
_GUION_ENFOQUES_CARRUSEL = [("una foto puntual del carrusel", 12),
                            ("cómo están elegidas u ordenadas las fotos", 4)]
_GUION_ENFOQUES_FOTO = [("la foto en sí (luz, encuadre, lugar)", 10)]


def _guion_enfoques(is_video: bool, n_imagenes: int) -> list[tuple[str, int]]:
    if is_video:
        return _GUION_ENFOQUES_BASE + _GUION_ENFOQUES_VIDEO
    if n_imagenes > 1:
        return _GUION_ENFOQUES_BASE + _GUION_ENFOQUES_CARRUSEL
    return _GUION_ENFOQUES_BASE + _GUION_ENFOQUES_FOTO
_GUION_VOCES = [
    "fan de hace años", "recién descubre la cuenta", "alguien del mismo rubro",
    "escéptico/a", "entusiasta sin exagerar", "seco/a, va al grano",
    "reflexivo/a, escribe prolijo", "joven, tipea rápido", "adulto/a, formal",
    "gracioso/a", "curioso/a", "le tocó de cerca el tema",
]
# Más de esto seguido del mismo largo ya se lee como bloque.
_GUION_MAX_SEGUIDOS = 2

# En la prueba, un cliente con "solo su prompt" (que pide inglés en UNA línea)
# devolvió la tanda entera en castellano, dos veces. "El idioma que piden las
# instrucciones" lo leía como "el idioma en que ESTÁN las instrucciones", y 80
# fichas en español pesan más que una línea. Por eso se dice qué NO es el idioma.
_GUION_IDIOMA = (
    "- IDIOMA DE LOS COMENTARIOS: el guion y estas reglas están en español porque "
    "son internos, pero eso NO define el idioma. Los comentarios van en el idioma "
    "que el cliente pidió explícitamente arriba (por ejemplo \"en inglés\"); si "
    "no pidió ninguno, en el idioma del caption del post."
)


def _repartir(opciones: list[tuple[str, int]], n: int, rng) -> list[str]:
    """n etiquetas con las proporciones EXACTAS de los pesos, mezcladas. Un
    sorteo independiente por comentario podía dar, en una tanda de 30, cuatro
    largos o quince: se repartían como el azar quisiera y no como pide la ficha."""
    total = sum(p for _, p in opciones)
    cuotas = [(et, n * p / total) for et, p in opciones]
    out = [et for et, c in cuotas for _ in range(int(c))]
    # Los que faltan por redondeo van a las fracciones más grandes.
    resto = sorted(cuotas, key=lambda x: x[1] - int(x[1]), reverse=True)
    out += [et for et, _ in resto[: n - len(out)]]
    rng.shuffle(out)
    return out


def _romper_bloques(etiquetas: list[str], rng) -> list[str]:
    """Deshace las rachas de más de _GUION_MAX_SEGUIDOS iguales intercambiando
    con otra posición al azar. Un shuffle a veces deja cinco cortos seguidos, y
    eso es exactamente el "bloque evidente" que se quiere evitar."""
    et = list(etiquetas)
    for _ in range(len(et) * 4):
        for i in range(_GUION_MAX_SEGUIDOS, len(et)):
            if len(set(et[i - _GUION_MAX_SEGUIDOS:i + 1])) == 1:
                j = rng.randrange(len(et))
                et[i], et[j] = et[j], et[i]
                break
        else:
            return et
    return et


def _es_corto(largo: str) -> bool:
    return largo.startswith("corto")


def _emparejar(largos: list[str], tipos: list[str], rng) -> list[str]:
    """Reubica los tipos que no calzan con su largo (ver _GUION_NO_CORTO)
    intercambiándolos con otra posición donde los dos queden bien. Si no hay con
    quién (tanda chiquita), se deja: el modelo lo resuelve mejor que una ficha
    imposible, y el prompt ya dice que el estilo manda."""
    tipos = list(tipos)

    def calza(i, t):
        return (t not in _GUION_NO_CORTO) if _es_corto(largos[i]) else (t not in _GUION_SOLO_CORTO)

    orden = list(range(len(tipos)))
    for i in range(len(tipos)):
        if calza(i, tipos[i]):
            continue
        rng.shuffle(orden)
        for j in orden:
            if j != i and calza(i, tipos[j]) and calza(j, tipos[i]):
                tipos[i], tipos[j] = tipos[j], tipos[i]
                break
    return tipos


def _guion_tanda(client_gender, cantidad: int = 0, rng=None, is_video: bool = False,
                 n_imagenes: int = 1) -> str:
    """El bloque del guion para ESTA tanda (va en el tramo no cacheado: cambia
    en cada llamada, que es la idea)."""
    rng = rng or random.Random()
    n = cantidad if cantidad and cantidad > 0 else _COMENTARIOS_DEFAULT
    largos = _romper_bloques(_repartir(_GUION_LARGOS, n, rng), rng)
    tipos = _emparejar(largos, _romper_bloques(_repartir(_GUION_TIPOS, n, rng), rng), rng)
    enfoques = _repartir(_guion_enfoques(is_video, n_imagenes), n, rng)
    voces = [_GUION_VOCES[i % len(_GUION_VOCES)] for i in range(n)]
    rng.shuffle(voces)

    lineas = [f"{i + 1}. {largos[i]} · {tipos[i]} · sobre {enfoques[i]} · voz: {voces[i]}"
              for i in range(n)]

    # Mismo corte que el FORMATO DE SALIDA: en mixto, primero la mitad de
    # mujeres y después la de hombres. Va en el texto y NO como una línea
    # "(sección mujeres:)" dentro de la lista: el modelo copia esas líneas tal
    # cual y a mitad de tanda ya no hay filtro de preámbulo que las frene, así
    # que se publicaban como un comentario más.
    secciones = ""
    if client_gender not in ("male", "female"):
        corte = (n + 1) // 2
        secciones = (f"- Las fichas 1 a {corte} van debajo de \"mujeres:\" y las "
                     f"{corte + 1} a {n} debajo de \"hombres:\".\n")

    return (
        "\n\nGUION DE ESTA TANDA (sorteado por la herramienta):\n"
        "Cada comentario tiene su ficha, en el mismo orden en que los vas a "
        "escribir. La ficha dice la ESTRUCTURA (largo, tipo, a qué reacciona, "
        "quién lo escribe); el ESTILO (idioma, tono, mayúsculas, emojis, "
        "vocabulario, temas) lo siguen mandando las instrucciones de arriba, y si "
        "una ficha choca con ellas, gana lo de arriba en ese punto.\n"
        # En la prueba, un cliente con "solo su prompt" (que pedía inglés en una
        # sola línea) devolvió la tanda entera en español: 40 fichas en
        # castellano pesan más que una instrucción.
        + _GUION_IDIOMA + "\n"
        "- Corto no es una etiqueta ni una palabra suelta: \"Gris\", \"You\" o "
        "\"Notebook brand\" no son comentarios. Un corto es algo que una persona "
        "tipearía y se entiende solo (un veredicto, una exclamación, una pregunta "
        "rápida).\n"
        "- Seguí el orden de las fichas. Si el post no tiene lo que pide el "
        "enfoque, elegí otro detalle REAL del post: nunca lo inventes.\n"
        "- La voz cambia cómo escribe cada uno (vocabulario, puntuación, energía), "
        "no es para nombrarla.\n"
        "- Que no haya dos comentarios que arranquen con la misma palabra ni con "
        "la misma estructura.\n"
        # Con las fichas "sobre algo que se VE", el pro abría 9 de 40 con "The
        # dog…/The whiteboard…/The order…": nombrar el detalle de entrada es el
        # arranque de plantilla. La gente lo mete en el medio o al final.
        "- Arrancar nombrando el detalle (\"The dog…\", \"That whiteboard…\", "
        "\"El café…\") es el arranque de plantilla: como mucho 3 en toda la "
        "tanda. En el resto, el detalle va en el medio o al final.\n"
        + secciones +
        "- En la salida NO van los números ni las fichas: solo el texto de cada "
        "comentario.\n"
        "---\n" + "\n".join(lineas) + "\n---\n"
        # Repetido al final a propósito: es lo último que lee antes de escribir,
        # justo después de 80 líneas en castellano.
        + _GUION_IDIOMA
    )


def _trae_formato_propio(template: str) -> bool:
    """¿El prompt ya trae embebido el bloque de FORMATO DE SALIDA, y entonces el
    sistema no tiene que agregar el suyo?

    La marca NO puede ser solo la frase "formato de salida". Un cliente escribió
    su prompt en markdown con una sección "## 📋 Formato de Salida" que habla del
    largo de los comentarios, y esa coincidencia de palabras apagaba el bloque
    del sistema ENTERO. Con él se iban los encabezados de género (el CRM se queda
    sin saber de quién es cada comentario), la cantidad a generar, y las reglas
    que prohíben numeración, títulos de categoría, @handles inventados y
    explicaciones. Sin esas reglas el modelo encabezaba la tanda con
    "comentarios:" o con una lectura del post en voz alta, y eso terminaba
    publicado como el comentario número 1.

    Lo que hace que un prompt REEMPLACE al bloque del sistema es que defina los
    encabezados de género: son lo único de ese bloque que no se puede perder, así
    que un prompt que no los nombra no puede estar reemplazándolo.
    """
    t = (template or "").lower()
    return "formato de salida" in t and ("hombres:" in t or "mujeres:" in t)


def _load_prompt(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, account_id: int | None = None, has_image: bool = False, client_gender=None, n_imagenes: int = 1, keyword: str = "", cantidad: int = 0) -> str:
    """El prompt completo, en un solo string. Es lo que se usaba siempre; hoy
    quedó como envoltorio de _load_prompt_partes para no romper llamadores."""
    partes = _load_prompt_partes(
        caption, comentarios_existentes, client_id, transcription, photo_description,
        is_video, evitar, account_id, has_image, client_gender, n_imagenes, keyword,
        cantidad)
    return "".join(p for p in (partes.template, partes.contexto, partes.tanda) if p)


class _Prompt:
    """El prompt partido en tres tramos, del más estable al más volátil. La razón
    es el CACHÉ DE PROMPT: la API cobra los tokens ya cacheados a ~0.1x, pero el
    match es por PREFIJO — un byte distinto invalida todo lo que sigue. Partirlo
    así hace que cada tramo se reuse todo lo que puede:

      template — el genérico + el prompt del cliente. Igual para TODOS los posts
                 de ese cliente: es el tramo que pega en la tanda del post
                 siguiente.
      contexto — caption, descripción visual, transcripción, notas del mosaico.
                 Igual para todas las tandas del MISMO post: es el que pega en
                 "Cargar más" y en los reintentos (que hoy re-pagan todo).
      tanda    — los comentarios a evitar y el formato de salida. Cambia en cada
                 llamada, así que no se cachea nunca.
    """

    def __init__(self, template: str, contexto: str, tanda: str, cacheable: bool):
        self.template = template
        self.contexto = contexto
        self.tanda = tanda
        # False cuando el template trae {caption}/{comentarios_existentes}
        # embebidos: ahí el texto del post queda INTERCALADO en el template, el
        # tramo estable deja de ser estable y cachear solo pagaría el recargo de
        # escritura (1.25x) sin lecturas. Los prompts nuevos no tienen marcadores.
        self.cacheable = cacheable


def _load_prompt_partes(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, account_id: int | None = None, has_image: bool = False, client_gender=None, n_imagenes: int = 1, keyword: str = "", cantidad: int = 0) -> _Prompt:
    # Modo keyword: camino aparte y completo (ver KEYWORD_CLIENT_ID). Nada del
    # contexto del post entra acá. No se cachea: son ~500 tokens, por debajo del
    # mínimo cacheable de la API, así que el marcador no haría nada.
    if keyword.strip():
        return _Prompt(_keyword_prompt(keyword, evitar), "", "", cacheable=False)

    template = _load_template(client_id, account_id)

    if not is_video:
        template = template.replace("un reel de Instagram", "un post/foto de Instagram")
        template = template.replace("El reel dice", "El post dice")

    existentes_str = "\n".join(comentarios_existentes) if comentarios_existentes else "(sin comentarios)"

    # El usuario del panel escribe SOLO sus instrucciones, sin variables. El contexto
    # del post (texto, comentarios, imagen, audio) lo agrega el sistema por detrás.
    #
    # Compatibilidad: los prompts viejos traen los marcadores {caption} /
    # {comentarios_existentes} embebidos en un lugar específico. Si están, se
    # sustituyen ahí (comportamiento exacto de siempre). Si NO están (prompt nuevo
    # escrito en lenguaje natural), el texto y los comentarios se agregan al final.
    tiene_caption_tok = "{caption}" in template
    tiene_coments_tok = "{comentarios_existentes}" in template
    # Reemplazo dirigido (no .format): otras llaves { } que escriba el usuario no rompen nada.
    prompt_template = (template
                       .replace("{caption}", caption)
                       .replace("{comentarios_existentes}", existentes_str))

    # Desde acá se arma el tramo del CONTEXTO DEL POST (ver _Prompt): todo lo que
    # es igual para todas las tandas de este post pero distinto entre posts.
    prompt = ""

    if not tiene_caption_tok and caption:
        prompt += f"\n\nTexto del post (caption):\n---\n{caption}\n---"
    if not tiene_coments_tok and comentarios_existentes:
        prompt += f"\n\nComentarios reales del post (matchear su tono corto/casual):\n---\n{existentes_str}\n---"

    if photo_description:
        prompt += f"\n\nDescripción visual de la foto/imagen:\n---\n{photo_description}\n---"

    # Los mensajes de error vienen entre paréntesis ("(transcripción no disponible: ...)"):
    # se le muestran al usuario, pero NO se le mandan al modelo como si fueran el audio.
    if transcription and not transcription.strip().startswith("("):
        prompt += f"\n\nTranscripción del audio del video:\n---\n{transcription}\n---"

    # La descripción visual la escribimos NOSOTROS (es una nota para el vendedor),
    # pero el modelo la tomaba como si fuera texto del post y copiaba las palabras
    # tal cual: cuando la nota estaba en español, en una tanda en inglés salieron
    # "the mesa negra setup", "the banda en la cabeza is the real MVP". Un
    # comentario real de ese post nunca diría eso. La aclaración sigue haciendo
    # falta aunque hoy la nota vaya en inglés (IDIOMA_CONTENIDO): el post puede
    # estar en otro idioma.
    if photo_description:
        prompt += (
            "\n\nLA DESCRIPCIÓN VISUAL DE ARRIBA ES UNA NOTA INTERNA NUESTRA, escrita "
            f"en {IDIOMA_CONTENIDO_NOMBRE}. No es el post ni lo que se lee en pantalla.\n"
            "- Los comentarios van en el idioma del post, no en el de esa nota.\n"
            f"- PROHIBIDO copiar palabras o frases en {IDIOMA_CONTENIDO_NOMBRE} de esa "
            "nota dentro de un comentario que no está en ese idioma. Si comentás un "
            "detalle, nombralo en el idioma del post.\n"
            "- Nunca menciones que existe una descripción."
        )
    if transcription and not transcription.strip().startswith("("):
        prompt += (
            f"\n\nLa transcripción es lo que se dice en el video, en {IDIOMA_CONTENIDO_NOMBRE} "
            "(si el video se habla en otro idioma, viene traducida). Si un tramo quedó "
            "cortado o no se entiende, ignoralo: no comentes sobre eso ni lo completes "
            "inventando."
        )

    if has_image:
        # El modelo recibe la imagen real del post como bloque multimodal (arriba).
        prompt += (
            "\n\nIMPORTANTE: arriba tenés la IMAGEN real del post. Miralas de verdad y "
            "basá los comentarios en detalles CONCRETOS que se ven (personas, gestos, "
            "expresiones, ropa, accesorios, lugar, objetos, comida, texto en pantalla, "
            "fondo). Nada de comentarios genéricos que servirían para cualquier foto."
        )

    if n_imagenes > 1:
        # La imagen es una grilla numerada que armamos nosotros, y la descripción
        # visual viene como lista "1. ... 2. ...". Sin esta aclaración el modelo
        # lo tomaba literal y escribía "the profile shot in 4 is clean" o "that
        # hair flip in frame 5" — nadie que mira el post ve numeritos ni frames.
        que_son = ("capturas de distintos momentos del video"
                   if is_video else "las fotos del carrusel")
        prompt += (
            f"\n\nLA IMAGEN DE ARRIBA ES UN MOSAICO armado por nosotros con {que_son}. "
            "La grilla y los números NO existen en el post: nadie que lo mira ve eso. "
            "PROHIBIDO en los comentarios: mencionar frames, capturas, slides, "
            "\"la foto/imagen N\", \"la primera/segunda/última\", el collage, la grilla "
            "o cualquier numeración. Comentá como alguien que "
            + ("miró el video entero de corrido."
               if is_video else "pasó el carrusel con el dedo.")
        )

    # "Cargar más": el usuario ya tiene una tanda de comentarios. Le pasamos esa
    # tanda para que el modelo NO la repita ni la parafrasee (era la causa de los
    # casi-duplicados entre tandas: cada llamada es stateless y sin esto re-inventa
    # variaciones de lo mismo).
    contexto = prompt

    # Desde acá, el tramo de LA TANDA: lo único que cambia entre un "Generar" y un
    # "Cargar más" del mismo post. Va al final para que todo lo anterior sea un
    # prefijo reusable.
    prompt = ""

    if evitar:
        # Uno por línea y SIN guiones: la lista va como ejemplo de formato aunque
        # no lo sea. Con "- " adelante, el modelo devolvía la tanda nueva en viñetas
        # ("- man really out here...") y los guiones terminaban publicados.
        evitar_str = "\n".join(str(c).strip() for c in evitar)
        prompt += (
            "\n\nATENCIÓN — ESTA ES UNA TANDA ADICIONAL. Los comentarios de abajo YA "
            "se generaron en una tanda anterior. Generá comentarios COMPLETAMENTE "
            "NUEVOS y DISTINTOS: prohibido repetirlos o parafrasearlos (no vale "
            "cambiar una palabra, abreviar, traducir ni agregar/quitar un emoji). "
            "Tienen que aportar ideas, vocabulario y estructuras diferentes.\n"
            "Comentarios ya generados (NO repetir ni parafrasear):\n---\n"
            f"{evitar_str}\n---"
        )

    # Formato de salida: SIEMPRE lo pone el sistema según el género del cliente.
    # No es editable desde el panel. Solo se saltea si el prompt ya lo trae
    # embebido (prompts viejos con el bloque adentro siguen funcionando).
    if not _trae_formato_propio(template):
        # El guion va solo con el formato del sistema: es el que garantiza el
        # orden de las secciones de género con el que se numeran las fichas.
        if _GUION_ON:
            prompt += _guion_tanda(client_gender, cantidad, is_video=is_video,
                                   n_imagenes=n_imagenes)
        prompt += _system_output_format(client_gender, cantidad)

    # Un template con marcadores lleva el caption adentro: deja de ser estable
    # entre posts y cachearlo sería pagar el recargo de escritura sin lecturas.
    cacheable = not (tiene_caption_tok or tiene_coments_tok)
    return _Prompt(prompt_template, contexto, prompt, cacheable)


# Viñeta o numeración al principio de la línea. El prompt pide "sin numeración,
# sin guiones", pero el modelo igual las mete cada tanto (sobre todo en las tandas
# adicionales) y eso se publicaba tal cual: "- man really out here in a gym fit".
# Un comentario de verdad no arranca con un guión suelto, así que se saca acá y
# no dependemos de que el modelo obedezca.
_VINETA_RE = re.compile(r"^\s*(?:[-–—*•·]+|\d{1,3}[.)])\s+")


def _sin_vineta(linea: str) -> str:
    limpio = _VINETA_RE.sub("", linea, count=1).strip()
    # Si la línea era SOLO la viñeta, no la vaciamos: que decida el llamador.
    return limpio or linea.strip()


# Markdown que ENVUELVE la línea entera: "**Comentarios:**", "__hombres:__",
# "### Comentarios", "*qué capo*". Mismo problema que las viñetas: el FORMATO DE
# SALIDA pide "sin títulos de categoría" y el modelo igual encabeza la tanda,
# así que la etiqueta se publicaba como el comentario número 1.
#
# Tiene que envolver la línea COMPLETA a propósito: un asterisco o un guión bajo
# sueltos en el medio son parte del comentario, y sobre todo son parte de las
# @menciones (@juan_perez no puede quedar como @juanperez).
_ENVOLTURA_MD_RE = re.compile(
    r"^\s*(?:\#{1,6}\s+(?P<h>.+?)|(?P<m>\*\*|__|\*|_)(?P<t>.+?)(?P=m))\s*$")


# Variantes del encabezado de género que el modelo escribe igual aunque el
# FORMATO DE SALIDA pida la palabra exacta ("Men:", "mujer :", "MALE:"). El
# front ya las tolera (generoDeHeader en app.js); acá hacen falta por el filtro
# de preámbulo de abajo, que si no se llevaría puesto un header mal escrito —
# y sin header todos los comentarios caen en una sola sección.
# El par canónico no se redefine acá: lo pone common/ordenes, que es quien
# después arma las órdenes del CRM.
_HEADER_ALIAS = {
    "mujer": "mujeres:", "women": "mujeres:", "female": "mujeres:",
    "hombre": "hombres:", "men": "hombres:", "male": "hombres:",
}
# La línea COMPLETA tiene que ser el marcador (una palabra + ":"), no un
# comentario que arranque con esa palabra.
_HEADER_RE = re.compile(r"^([a-zñáéíóú]+)\s*:$")


def _header_genero(texto: str) -> str:
    """El encabezado de sección en su forma canónica ("hombres:" / "mujeres:"),
    o "" si la línea no es un encabezado."""
    t = (texto or "").strip().lower()
    if es_header_genero(t):
        return t
    m = _HEADER_RE.match(t)
    return _HEADER_ALIAS.get(m.group(1), "") if m else ""


def _es_preambulo(texto: str) -> bool:
    """¿Es algo que el modelo dijo ANTES de arrancar, y no un comentario?

    Son dos formas, las dos con dos puntos:
      - la etiqueta sola ("comentarios:", "output:"), sin nada debajo;
      - la lectura del post en voz alta ("lectura de tono: post de operator/CEO,
        Cadence entrando fuerte a retail...").

    Que la etiqueta con contenido tenga que ser de VARIAS palabras no es un
    detalle: "pov: se lo bancó" o "day 3: sigo igual" son comentarios de verdad
    y arrancan igual. Aun así este criterio es agresivo, y por eso el llamador
    lo aplica solo mientras no aceptó nada todavía (ver `preambulo`).
    """
    etiqueta, sep, resto = texto.partition(":")
    if not sep or len(etiqueta) > 60:
        return False
    if not resto.strip():
        return True
    return len(etiqueta.split()) >= 2


def _limpiar_linea(linea: str, preambulo: bool = False) -> str:
    """Una línea del stream -> el comentario listo para publicar, o "" si hay
    que descartarla.

    Saca la viñeta, desenvuelve el markdown y tira las ETIQUETAS: una línea que
    el modelo resaltó y que termina en dos puntos es un título de sección, no un
    comentario. Si está resaltada pero NO termina en dos puntos es un comentario
    al que le puso énfasis: se queda, sin las marcas (nadie escribe "**qué
    capo**" abajo de un post).

    `preambulo` = todavía no se aceptó ninguna línea de esta generación. Ahí
    entra además el filtro de _es_preambulo, que saca lo que el modelo escribe
    antes de empezar. Se apaga con la primera línea buena porque no hay forma de
    distinguir esas etiquetas de un comentario que arranca igual, y en el medio
    de la tanda el comentario es lo probable.

    Excepción en todos los casos: "hombres:" / "mujeres:" son parte del formato
    y el CRM los necesita para saber el género de cada bloque, así que vuelven
    canónicos en vez de descartarse.
    """
    base = _sin_vineta(linea)
    m = _ENVOLTURA_MD_RE.match(base)
    if m:
        base = (m.group("h") or m.group("t")).strip()
        if base.endswith(":") and not _header_genero(base):
            return ""
    header = _header_genero(base)
    if header:
        return header
    if preambulo and _es_preambulo(base):
        print(f"[ai] preámbulo del modelo, lo descarto: {base[:70]!r}", flush=True)
        return ""
    return base


# Todo lo que no sea letra, número o espacio: emojis, puntuación, comillas. Dos
# comentarios que solo se diferencian en el emoji del final son el mismo
# comentario para quien lee el post.
_NO_ALFANUM_RE = re.compile(r"[^\w\s]", re.UNICODE)
# Alargues del tipo "holaaaa" / "jajajaaa": mismo comentario escrito distinto.
# Se colapsan las corridas de 3 o más, no las de 2, para no pisar los dobles
# legítimos del español (carro, calle, acción).
# Solo LETRAS: con los dígitos adentro, "1000%" se reducía a "10%" y quedaba
# como repetido de un comentario que decía otra cosa.
_ALARGUE_RE = re.compile(r"([^\W\d_])\1{2,}", re.UNICODE)


def _clave_dedup(texto: str) -> str:
    """Clave para decidir si dos comentarios son "el mismo".

    No alcanza con comparar el texto crudo: el modelo repite la misma idea
    cambiando el emoji, una tilde o una mayúscula, y eso publicado en el mismo
    post se lee como lo que es (un bot). Se normaliza a minúsculas, sin tildes,
    sin emojis ni puntuación y sin alargues.

    Si después de limpiar no queda nada (un comentario que es SOLO emojis), se
    cae al texto crudo en minúsculas: ahí el emoji es el comentario, y dos "🔥🔥"
    sí son duplicados entre sí, pero "🔥" y "😍" no.
    """
    base = unicodedata.normalize("NFD", (texto or "").lower())
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")   # sin tildes
    base = _NO_ALFANUM_RE.sub(" ", base)
    base = _ALARGUE_RE.sub(r"\1", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or (texto or "").strip().lower()


class _Rechazo(Exception):
    """La IA rechazó el pedido por políticas.

    No es un error de red ni saturación: la API contesta 200 con el contenido
    vacío y stop_reason="refusal". Reintentar es inútil (el mismo pedido se
    rechaza igual) y hace perder ~10 segundos de backoff para terminar mostrando
    "generación cortada", que miente sobre la causa. Con esta excepción se corta
    en el primer intento y el vendedor se entera del motivo real.
    """


# Si una generación devuelve menos de esto, asumimos que el stream se cortó
# (throttling / corte prematuro de la API). Antes se descartaba TODO y se
# regeneraba de cero: 19 comentarios buenos a la basura y se re-pagaba la salida
# completa (que es el lado caro y no se cachea). Ahora, si llegó algo aprovechable
# se completa la tanda pidiendo SOLO los que faltan (ver _MIN_PARA_COMPLETAR).
_MIN_COMENTARIOS = 20
_MAX_INTENTOS = 4

# Qué fracción de lo pedido alcanza para dar la tanda por buena. El modelo entrega
# ~50 comentarios por llamada por más que se le pidan 84, y el corte de "tanda
# completa" era _MIN_COMENTARIOS (20): con 51 en la mano se daba por terminada y
# al cliente que compra 70 le faltaban 20. Ahora, si no llega, se completa
# pidiendo SOLO los que faltan. La tolerancia evita gastar una llamada entera por
# los últimos 3 comentarios (la reserva del 20% ya cubre ese hueco).
_TOLERANCIA_TANDA = float(os.environ.get("CROW_TOLERANCIA_TANDA", "0.9"))

# Piso para "completar" en vez de "regenerar". Debajo de esto lo que llegó es tan
# poco que probablemente la llamada falló de entrada (no vale la pena arrastrar
# 2 comentarios), así que se descarta y se reintenta limpio.
_MIN_PARA_COMPLETAR = 8


# ── Perillas de costo ─────────────────────────────────────────────────────────
#
# Todo por env var para poder medir y ajustar sin redeploy. El thinking es el
# ítem más caro de la generación (más que los comentarios en sí), así que tiene
# que poder apagarse y compararse contra la calidad, no quedar clavado.

# Caché de prompt: cobra los tokens ya vistos a ~0.1x, pero la ESCRITURA cuesta
# 1.25x. Conviene cuando hay reuso (varios posts del mismo cliente seguidos,
# "Cargar más", reintentos) y es levemente contraproducente con tráfico muy
# espaciado. Mirá cache_read_tokens vs cache_creation_tokens en token_usage para
# saber cuál es tu caso: si la lectura no supera a la escritura, poné 0.
_PROMPT_CACHE = os.environ.get("CROW_PROMPT_CACHE", "1").strip() not in ("0", "false", "no")

# Cuánto "piensa" el modelo antes de escribir. Se prendió para bajar el bot-feel,
# y en la práctica es ~40% del costo del post. "off" lo apaga; los niveles válidos
# son low | medium | high | xhigh | max.
_THINKING = os.environ.get("CROW_THINKING", "adaptive").strip().lower()
_EFFORT = os.environ.get("CROW_EFFORT", "medium").strip().lower()

# Cupo de salida por llamada. OJO: el thinking sale de ACÁ, no de un presupuesto
# aparte. Con 8192 y effort medium, opus-4.8 planificando una tanda de 100+
# comentarios se comía el cupo entero PENSANDO y el stream terminaba con 0
# líneas de texto: se leía como "generación cortada", se quemaban los 4 intentos
# (~8 minutos y ~$0.85) y el vendedor no recibía nada. Los modelos actuales
# aceptan hasta 128k de salida y acá siempre se llama por stream, así que no hay
# riesgo de timeout HTTP. No es gasto extra: se paga la salida REAL, esto es
# solo el techo.
_MAX_TOKENS = int(os.environ.get("CROW_MAX_TOKENS", "32000"))

# ¿Se le manda la imagen también a la generación, o alcanza con la descripción
# visual en texto que ya generó la llamada de visión? Mandarla cuesta ~1100
# tokens por intento. En 1 se comporta como siempre; en 0 se ahorra eso a costa
# de que el modelo no vea la foto (solo la lea). Para A/B, no para prender y
# olvidarse.
_VISION_EN_GENERACION = os.environ.get("CROW_VISION_EN_GENERACION", "1").strip() not in ("0", "false", "no")

# Excepción de effort: quién genera con `low` en vez de _EFFORT. Existe para los
# de volumen muy alto, donde el thinking a effort normal se come el presupuesto
# sin que el resto de la base gaste nada parecido.
#
# Hay DOS listas porque hay dos claves y no siempre llegan las dos. En la
# práctica `user_id` viene vacío en casi todas las llamadas (el panel de tokens
# lo muestra como "sin identificar"), mientras que `account_id` sí llega: por eso
# la lista de cuentas es la que hace el trabajo, y la de usuarios queda para
# cuando la atribución por vendedor esté arreglada. Son listas separadas a
# propósito: un mismo número es una cuenta distinta que un usuario, y mezclarlos
# en una sola lista le bajaría el effort a quien no corresponde.
#
# OJO — CROW_EFFORT_LOW_ACCOUNTS agarra a la cuenta ENTERA, todos sus vendedores.
# Y esto hace que dos vendedores reciban tandas con distinta profundidad de
# planificación para el MISMO cliente pro: es una diferencia chica pero
# comparable entre ellos. Si aparece en quejas, la salida no es afinar la lista
# sino bajar _EFFORT para todos (y medirlo) o dejarlo como está. Listas cortas y
# temporales, no un mecanismo de tarifas encubierto.
def _lista_ids(var: str) -> frozenset:
    return frozenset(x.strip() for x in os.environ.get(var, "").split(",") if x.strip())


_EFFORT_LOW_USERS = _lista_ids("CROW_EFFORT_LOW_USERS")
_EFFORT_LOW_ACCOUNTS = _lista_ids("CROW_EFFORT_LOW_ACCOUNTS")


def _effort_para(user_id=None, account_id=None) -> str:
    """El effort que le toca a esta generación: `low` si el vendedor o su cuenta
    están en las listas de excepción, si no el global. Ambos llegan como int o
    None; un None no matchea nunca (si no, una llamada sin atribuir se llevaría
    el effort de la excepción)."""
    if user_id is not None and str(user_id) in _EFFORT_LOW_USERS:
        return "low"
    if account_id is not None and str(account_id) in _EFFORT_LOW_ACCOUNTS:
        return "low"
    return _EFFORT


def _extra_body(modo_keyword: bool, user_id=None, account_id=None) -> dict:
    """Los kwargs de thinking/effort que el SDK pineado (anthropic 0.54.0) no
    expone. En modo keyword no hay nada que planear (es la misma palabra N veces):
    pensar solo suma latencia y tokens.

    OJO — apagar el thinking es mandar `disabled` EXPLÍCITO, no omitir el campo.
    En claude-sonnet-5 (y en opus-5) el adaptive es el DEFAULT: si no se manda
    nada, el modelo piensa igual y se paga igual. Omitirlo solo apagaba el
    thinking en la generación de opus-4-8 y anteriores. Por eso el modo keyword
    venía pensando —y pagando— para escribir la misma palabra 15 veces.

    Cuando se apaga no se manda `effort`: en opus-5 la combinación de thinking
    apagado con effort xhigh/max devuelve 400, y sin el campo queda en el default
    (high), que es válido en todos los modelos.
    """
    if modo_keyword or _THINKING in ("off", "0", "no", "disabled", ""):
        return {"thinking": {"type": "disabled"}}
    return {"thinking": {"type": "adaptive"},
            "output_config": {"effort": _effort_para(user_id, account_id)}}


def _bloques(prompt: "_Prompt", image_b64: str, image_media_type: str) -> list | str:
    """Arma el `content` del mensaje, con los breakpoints del caché de prompt.

    El orden importa y no es casual: el caché matchea por PREFIJO, así que va de
    lo más estable a lo más volátil —

        [template del cliente] (breakpoint) [imagen] [contexto del post] (breakpoint) [tanda]

    El template va PRIMERO para que pegue entre posts distintos del mismo cliente
    (es lo que más se repite). La imagen va antes del contexto porque el contexto
    es el que dice "arriba tenés la IMAGEN": queda arriba de verdad.

    Sin caché o sin imagen devuelve la forma simple de siempre.
    """
    cachear = _PROMPT_CACHE and prompt.cacheable
    imagen = None
    if image_b64:
        imagen = {"type": "image", "source": {"type": "base64",
                                              "media_type": image_media_type or "image/jpeg",
                                              "data": image_b64}}
    # Sin nada que cachear ni imagen: un string pelado, como antes.
    if not cachear and imagen is None:
        return "".join(p for p in (prompt.template, prompt.contexto, prompt.tanda) if p)

    marca = {"cache_control": {"type": "ephemeral"}} if cachear else {}
    bloques: list = [{"type": "text", "text": prompt.template, **marca}]
    if imagen is not None:
        bloques.append(imagen)
    if prompt.contexto:
        # Segundo breakpoint: cierra "template + imagen + contexto", que es el
        # prefijo que comparten todas las tandas y todos los reintentos del mismo
        # post. Es el que hace que un "Cargar más" no re-pague el post entero.
        bloques.append({"type": "text", "text": prompt.contexto, **marca})
    if prompt.tanda:
        bloques.append({"type": "text", "text": prompt.tanda})
    return bloques


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, image_b64: str = "", image_media_type: str = "", client_gender=None, client_quality=None, n_imagenes: int = 1, keyword: str = "", shortcode: str = "", cantidad: int = 0, account_id=None, user_id=None) -> list[str]:
    comentarios: list[str] = []
    for tipo, data in generar_comentarios_stream(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar, image_b64=image_b64, image_media_type=image_media_type, client_gender=client_gender, client_quality=client_quality, n_imagenes=n_imagenes, keyword=keyword, shortcode=shortcode, cantidad=cantidad, account_id=account_id, user_id=user_id):
        if tipo == "reset":
            comentarios = []          # la corrida anterior salió cortada: descartamos
        elif tipo == "comentario":
            comentarios.append(data)
    return comentarios


def generar_comentarios_stream(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, image_b64: str = "", image_media_type: str = "", client_gender=None, client_quality=None, n_imagenes: int = 1, keyword: str = "", shortcode: str = "", cantidad: int = 0, account_id=None, user_id=None):
    """Yields (tipo, data): ("chunk", texto_parcial), ("comentario", linea_completa),
    ("descartado", linea_repetida) o ("reset", None) cuando una generación salió
    cortada y se reintenta desde cero (el consumidor debe descartar lo emitido
    hasta ese punto).

    "descartado" es un comentario repetido que NO se entrega: el consumidor solo
    tiene que limpiar el texto parcial que venía mostrando de esa línea.

    evitar: comentarios de tandas anteriores que el modelo no debe repetir ni
    parafrasear (usado por "Cargar más").
    image_b64/image_media_type: imagen del post (visión multimodal). Si viene, se
    manda como bloque de imagen a Claude junto con el prompt.
    client_quality: 'pro' | 'standard' — decide con qué modelo se genera.
    keyword: modo keyword — la tanda es N veces esa palabra variando la
    escritura, sin contexto del post (ver KEYWORD_CLIENT_ID)."""
    keyword = (keyword or "").strip()
    modo_keyword = bool(keyword)
    # En modo keyword la imagen no se manda aunque venga: no aporta nada a
    # escribir una palabra y se paga igual. _VISION_EN_GENERACION la saca también
    # del resto de los posts (queda solo la descripción visual en texto).
    has_image = bool(image_b64) and not modo_keyword and _VISION_EN_GENERACION
    # Escribir la misma palabra 40 veces no mejora con el modelo caro.
    modelo = _MODEL_STANDARD if modo_keyword else _modelo(client_quality)
    print(f"[ai] calidad={client_quality or 'standard'} modelo={modelo}"
          + (f" effort={_effort_para(user_id, account_id)}" if not modo_keyword else "")
          + f" user={user_id} account={account_id}"
          + (f" keyword={keyword!r}" if modo_keyword else ""), flush=True)
    prompt = _load_prompt_partes(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar, has_image=has_image, client_gender=client_gender, n_imagenes=n_imagenes, keyword=keyword, cantidad=cantidad)

    # Cuántos comentarios se pidieron de verdad. Sirve para dos cosas: saber
    # cuántos faltan si la tanda queda corta, y no pedir 70 cuando el cliente
    # publica 40 (la salida es el lado caro y no se cachea).
    cantidad_pedida = KEYWORD_CANTIDAD if modo_keyword else (
        cantidad if cantidad and cantidad > 0 else _COMENTARIOS_DEFAULT)

    # Cuántos hacen falta para dar la tanda por terminada. En modo palabra clave
    # el modelo aporta las FORMAS de escribir la palabra y la cantidad la ponemos
    # nosotros repitiendo, así que no tiene sentido exigirle el total.
    # En modo normal es lo que se pidió (menos la tolerancia): si no llega, se
    # completa pidiendo solo los que faltan en vez de entregar una tanda corta.
    suficiente = (max(1, int(KEYWORD_CANTIDAD * 0.75)) if modo_keyword
                  else max(_MIN_COMENTARIOS, int(cantidad_pedida * _TOLERANCIA_TANDA)))

    content = _bloques(prompt, image_b64 if has_image else "", image_media_type)
    extra = _extra_body(modo_keyword, user_id, account_id)

    # Comentarios que ya se le entregaron al consumidor y NO se van a descartar.
    # Si la tanda queda corta, la vuelta siguiente los pasa como "a evitar" y pide
    # solo los que faltan, en vez de tirar todo y re-pagar la salida completa.
    acumulados: list[str] = []

    # Antirrepetidos. El prompt ya pide no repetir (y en "Cargar más" le pasamos
    # la tanda anterior en `evitar`), pero el modelo igual manda el mismo
    # comentario dos veces —o el mismo con otro emoji—, y eso se publicaba tal
    # cual: dos comentarios idénticos abajo del mismo post.
    #
    # El repetido se DESCARTA y no cuenta para el total, así que la tanda queda
    # corta y el mecanismo de completar (más abajo) pide justo los que faltan,
    # con los ya entregados en la lista de "no repetir". Es decir: se tiran los
    # repetidos y se generan nuevos, sin re-pagar la tanda entera.
    #
    # En modo palabra clave la repetición NO es un defecto (todos los
    # comentarios son la misma palabra y lo que varía es cómo está escrita), así
    # que ahí se comparan las FORMAS tal cual: se descarta "toolkit" dos veces,
    # pero "TOOLKIT" y "toolkit" son formas distintas y las dos sirven.
    def _clave(texto: str) -> str:
        return texto.strip() if modo_keyword else _clave_dedup(texto)

    # Las tandas anteriores ("Cargar más") también cuentan como ya vistas. En
    # modo keyword no: ahí volver a mandar la misma forma es lo esperado.
    semilla = set() if modo_keyword else {_clave(c) for c in (evitar or []) if c.strip()}
    descartados = 0

    # ANCLA DEL ENCABEZADO. El FORMATO DE SALIDA obliga a que la PRIMERA línea
    # sea "hombres:" / "mujeres:", así que todo lo que llegue antes no es un
    # comentario: es el modelo hablando. Es un ancla estructural y no depende de
    # adivinar la forma del preámbulo, que es lo que _es_preambulo no puede
    # cubrir: la tanda real de un cliente arrancó con "WOW no ese es el enunciado
    # del prompt..." (el modelo copiando un ejemplo del prompt y frenándose) y
    # después "Genero los comentarios:", y las dos líneas se publicaron.
    # En modo keyword no hay encabezado, así que no hay ancla.
    pide_header = not modo_keyword
    # Si el modelo NO emite encabezado, lo retenido son comentarios de verdad: se
    # sueltan al pasar este tope (o al terminar el stream), nunca se pierden.
    _MAX_ANTES_HEADER = 5

    prev_motivo = None
    hubo_error = False   # la vuelta anterior falló por API (≠ entregó de menos)
    # Se puede degradar en caliente (ver el corte por max_tokens más abajo): si
    # una vuelta se fue entera en thinking, la siguiente piensa menos en vez de
    # repetir el mismo fracaso otras tres veces.
    extra_intento = extra
    for intento in range(1, _MAX_INTENTOS + 1):
        if intento > 1:
            print(f"[ai] {prev_motivo}, reintento {intento}/{_MAX_INTENTOS}"
                  + (f" — completando (ya hay {len(acumulados)})" if acumulados else ""),
                  flush=True)
            if hubo_error:
                time.sleep(3 * (intento - 1))  # backoff: overloaded suele ser transitorio
                hubo_error = False
            if acumulados:
                # COMPLETAR: no se emite "reset", así que el consumidor conserva
                # lo que ya mostró y esta vuelta solo agrega lo que falta. El
                # prompt se rearma con los acumulados en la lista de "no repetir"
                # (el mismo mecanismo de "Cargar más").
                prompt = _load_prompt_partes(
                    caption, comentarios_existentes, client_id, transcription,
                    photo_description, is_video, (evitar or []) + acumulados,
                    has_image=has_image, client_gender=client_gender,
                    n_imagenes=n_imagenes, keyword=keyword,
                    cantidad=max(1, cantidad_pedida - len(acumulados)))
                content = _bloques(prompt, image_b64 if has_image else "", image_media_type)
            else:
                yield ("reset", None)

        count = 0
        buffer = ""
        # Arrancan así en CADA vuelta: el reintento genera de cero y trae su
        # propio preámbulo. `preambulo` se apaga con la primera línea aceptada.
        preambulo = True
        esperando_header = pide_header
        retenidas: list[str] = []   # llegaron antes del encabezado (ver el ancla)
        nuevos: list[str] = []      # lo de ESTA vuelta (por si hay que conservarlo)

        def _aceptar(texto: str):
            """Dedup + contabilidad de una línea ya limpia. Es un generador: el
            llamador hace `yield from`."""
            nonlocal count, descartados
            clave = _clave(texto)
            if clave in vistos:
                descartados += 1
                print(f"[ai] repetido, lo descarto: {texto[:60]!r}", flush=True)
                # El consumidor venía mostrando esta línea a medida que llegaba:
                # hay que decirle que la borre.
                yield ("descartado", texto)
                return
            vistos.add(clave)
            count += 1
            nuevos.append(texto)
            yield ("comentario", texto)

        def _soltar_retenidas(motivo: str):
            """El encabezado no llegó: lo retenido eran comentarios."""
            nonlocal esperando_header
            esperando_header = False
            if not retenidas:
                return
            print(f"[ai] {motivo}: suelto {len(retenidas)} línea(s) retenida(s)", flush=True)
            pendientes, retenidas[:] = retenidas[:], []
            for t in pendientes:
                yield from _aceptar(t)
        # Se rearma en cada vuelta a partir de lo que el consumidor TIENE en
        # pantalla: si hubo "reset" (acumulados vacío), lo de la vuelta anterior
        # ya no existe para nadie y volver a generarlo es legítimo. Si no se
        # rearmara, un corte de stream en el primer intento dejaría marcada como
        # repetida a media tanda buena y la regeneración saldría vacía.
        vistos = set(semilla) | {_clave(c) for c in acumulados}
        try:
            with _client.messages.stream(
                model=modelo,
                # El "pensar" también consume de este cupo — ver _MAX_TOKENS.
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": content}],
                # thinking/effort: el modelo planea la distribución de largos y
                # voces ANTES de escribir, para bajar el "bot-feel". Es el ítem
                # más caro de la llamada (~40% del costo del post), así que sale
                # de CROW_THINKING / CROW_EFFORT y se puede apagar o bajar sin
                # redeploy — ver _extra_body.
                # Va por extra_body porque el SDK pineado (anthropic 0.54.0) no
                # expone estos kwargs; extra_body los inyecta en el body.
                extra_body=extra_intento,
            ) as stream:
                for text in stream.text_stream:
                    buffer += text
                    yield ("chunk", text)
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        line = _limpiar_linea(line, preambulo)
                        if not line:
                            continue
                        preambulo = False
                        if esperando_header:
                            if _header_genero(line):
                                # Llegó el ancla: lo de antes era el modelo
                                # hablando y se va.
                                esperando_header = False
                                if retenidas:
                                    print(f"[ai] {len(retenidas)} línea(s) antes del "
                                          f"encabezado, las descarto: "
                                          f"{retenidas[0][:70]!r}", flush=True)
                                    retenidas.clear()
                            else:
                                retenidas.append(line)
                                if len(retenidas) <= _MAX_ANTES_HEADER:
                                    continue
                                yield from _soltar_retenidas(
                                    f"sin encabezado tras {_MAX_ANTES_HEADER} líneas")
                                continue
                        yield from _aceptar(line)
                # Los tokens se leen del mensaje final, ya adentro del `with`.
                # Acá es donde se ve cuánto pesa el thinking: entra en output_tokens.
                #
                # try propio a propósito: este bloque está dentro del try que
                # dispara los reintentos, y un error al MEDIR no puede provocar
                # que se regenere la tanda entera (sería el colmo: gastar el doble
                # por culpa del contador de gastos).
                try:
                    final = stream.get_final_message()
                    _registrar_uso("keyword" if modo_keyword else "generacion",
                                   modelo, final.usage,
                                   intento=intento, client_id=client_id,
                                   shortcode=shortcode,
                                   account_id=account_id, user_id=user_id)
                    stop = getattr(final, "stop_reason", None)
                except Exception as e:
                    print(f"[tokens] no se pudo medir la generación: {e}", flush=True)
                    stop = None
                # Rechazo por políticas: no se reintenta. Sin esto se leía como
                # "generación cortada" y se quemaban los 3 intentos con 9s de
                # backoff, para terminar mostrándole al vendedor un motivo falso.
                if stop == "refusal" and count == 0:
                    raise _Rechazo(
                        "La IA rechazó generar comentarios para este post. "
                        "Probá con otro post o avisá al administrador.")
                # El cupo se fue entero en thinking: el stream cerró en
                # max_tokens sin una sola línea de texto. Reintentar igual da el
                # mismo resultado (mismo prompt, misma profundidad), así que la
                # vuelta siguiente piensa menos. Sin esto se queman los 4
                # intentos con ~2 minutos cada uno para no entregar nada.
                if (stop == "max_tokens" and count == 0 and not acumulados
                        and extra_intento.get("thinking", {}).get("type") == "adaptive"):
                    if extra_intento.get("output_config", {}).get("effort") != "low":
                        print("[ai] el thinking se comió el cupo de salida "
                              f"({_MAX_TOKENS} tokens, 0 líneas): bajo el effort "
                              "a low para el reintento", flush=True)
                        extra_intento = {**extra_intento,
                                         "output_config": {"effort": "low"}}
                    else:
                        print("[ai] cupo de salida agotado en thinking incluso "
                              "con effort low: apago el thinking para el "
                              "reintento", flush=True)
                        extra_intento = {"thinking": {"type": "disabled"}}
        except _Rechazo:
            raise
        except Exception as e:
            # overloaded_error y otros transitorios de la API: reintentar desde cero
            if intento == _MAX_INTENTOS:
                raise
            prev_motivo = f"error de API ({e})"
            hubo_error = True
            continue

        # El stream terminó sin encabezado: lo retenido no era preámbulo, eran
        # comentarios. Van ANTES del último (ese es el orden en que llegaron).
        if esperando_header:
            yield from _soltar_retenidas("el stream terminó sin encabezado")

        ultimo = _limpiar_linea(buffer, preambulo)
        if ultimo:
            yield from _aceptar(ultimo)

        total = len(acumulados) + count

        # tanda completa (o último intento): la damos por buena
        if total >= suficiente or intento == _MAX_INTENTOS:
            if total < suficiente:
                print(f"[ai] tanda entregada corta: {total} de {cantidad_pedida} "
                      f"(se agotaron los {_MAX_INTENTOS} intentos)", flush=True)
            if descartados:
                print(f"[ai] {descartados} repetidos descartados en total "
                      f"({total} comentarios únicos entregados)", flush=True)
            return

        # Quedó corta. Si lo que hay ya es aprovechable, se CONSERVA y la vuelta
        # siguiente pide solo el resto; si es casi nada, seguramente la llamada
        # falló de entrada y no vale la pena arrastrarlo: se descarta y se
        # reintenta limpio (el "reset" de arriba avisa al consumidor).
        if total >= _MIN_PARA_COMPLETAR:
            acumulados = acumulados + nuevos
            prev_motivo = (f"tanda corta ({total} de {cantidad_pedida}"
                           + (f", {descartados} repetidos descartados" if descartados else "")
                           + ")")
        else:
            acumulados = []
            prev_motivo = f"generación cortada ({total} líneas)"
            # Casi nada en la mano: la llamada se cortó de entrada (throttling,
            # corte de stream). Eso sí conviene esperarlo antes de reintentar.
            hubo_error = True


def describir_imagen(image_b64: str, image_media_type: str = "", caption: str = "",
                     n_imagenes: int = 1, es_video: bool = False,
                     shortcode: str = "", account_id=None, user_id=None,
                     client_id: str | None = None) -> str:
    """Describe textualmente la imagen de un post (para mostrarla al usuario como
    si fuera el pie de página). Llamada de visión corta, en el idioma de contenido
    (IDIOMA_CONTENIDO, inglés por default). Devuelve "" ante cualquier problema
    (el llamador simplemente no muestra descripción)."""
    if not image_b64:
        return ""
    qué_mirar = (
        "personas y gestos, ropa y accesorios, lugar, objetos, comida, "
        "cualquier texto que aparezca en la imagen, y el ambiente general"
    )
    if n_imagenes > 1:
        # Carrusel o video: llega UNA sola imagen que es una grilla numerada (las
        # fotos del carrusel, o capturas repartidas a lo largo del video). Una
        # sola llamada de visión — el costo es el de UNA imagen, no el de N —
        # pero la respuesta sí va item por item.
        if es_video:
            qué_es = (
                f"Esta imagen es un mosaico con {n_imagenes} capturas de un video "
                "de Instagram, tomadas a intervalos regulares y en orden "
                "cronológico (la 1 es el principio, la última es el final)"
            )
            unidad, conjunto = "captura", (
                f"Al final agregá una última línea que empiece con \"{_ETIQUETA_CONJUNTO}\" "
                "contando en 1 o 2 oraciones qué pasa en el video de principio a "
                "fin (cómo evoluciona la escena)."
            )
        else:
            qué_es = (
                f"Esta imagen es un mosaico con las {n_imagenes} fotos de un "
                "carrusel de Instagram, en orden"
            )
            unidad, conjunto = "foto", (
                f"Al final agregá una última línea que empiece con \"{_ETIQUETA_CONJUNTO}\" "
                "resumiendo de qué se trata el carrusel en 1 oración."
            )
        instruccion = (
            f"{qué_es}, ordenadas de izquierda a derecha y de arriba hacia abajo, "
            "cada una con su número arriba a la izquierda (el número está "
            "sobreimpreso por nosotros, no forma parte de la imagen).\n"
            f"Describí en {IDIOMA_CONTENIDO_NOMBRE} CADA {unidad} por separado: {qué_mirar}.\n"
            f"Formato EXACTO, una línea por {unidad} y nada más:\n"
            f"1. <descripción de la {unidad} 1, 1 o 2 oraciones>\n"
            f"2. <descripción de la {unidad} 2, 1 o 2 oraciones>\n"
            f"...hasta la {n_imagenes}.\n"
            f"{conjunto}\n"
            "Ignorá las zonas blancas vacías de la grilla (son relleno). Concreto "
            "y fiel a lo que se ve, sin preámbulos ni comillas."
        )
    else:
        instruccion = (
            f"Describí en {IDIOMA_CONTENIDO_NOMBRE}, en 2 a 5 oraciones, qué se ve en esta imagen de "
            f"Instagram (puede ser una foto, o la portada/preview de un video): {qué_mirar}. "
            "Concreto y fiel a lo que se ve. Devolvé SOLO la descripción, sin "
            "preámbulos ni comillas."
        )
    content = [
        {"type": "image", "source": {"type": "base64",
                                      "media_type": image_media_type or "image/jpeg",
                                      "data": image_b64}},
        {"type": "text", "text": instruccion},
    ]
    # Un 529/overloaded puntual dejaba al post sin descripción. Reintentamos una
    # vez antes de rendirnos. Si igual falla, PROPAGAMOS la excepción para que el
    # llamador sepa POR QUÉ (saturación/sin crédito) y se lo pueda avisar al
    # vendedor, en vez de quedar en silencio con la descripción vacía.
    ultimo_error = None
    for intento in (1, 2):
        try:
            resp = _client.messages.create(
                model=_MODEL_VISION,
                # Una línea por foto: con carruseles largos 500 tokens cortaban
                # la descripción a la mitad. OJO: el thinking (abajo) también
                # consumía de este cupo, así que parte de esos cortes eran el
                # modelo razonando, no la descripción siendo larga.
                max_tokens=500 + 150 * max(0, n_imagenes - 1),
                messages=[{"role": "user", "content": content}],
                # Thinking APAGADO explícito. Describir en 5 oraciones lo que se
                # ve en una foto no necesita planificación, y en claude-sonnet-5
                # omitir el campo NO lo apaga: el adaptive es el default. Sin
                # esto se pagaba razonamiento a precio de salida en cada post
                # —con effort `high`, que es el default cuando no se manda— para
                # una tarea trivial.
                extra_body={"thinking": {"type": "disabled"}},
            )
            _registrar_uso("descripcion", _MODEL_VISION, resp.usage,
                           intento=intento, shortcode=shortcode,
                           client_id=client_id,
                           account_id=account_id, user_id=user_id)
            # Rechazo por políticas: la API devuelve 200 con contenido vacío. No
            # se reintenta (el mismo pedido va a volver a ser rechazado): se
            # corta y el llamador le explica al vendedor por qué no hay
            # descripción, en vez de dejarlo en silencio.
            if getattr(resp, "stop_reason", None) == "refusal":
                raise _Rechazo("la IA rechazó describir esta imagen")
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except _Rechazo:
            raise
        except Exception as e:
            ultimo_error = e
            print(f"[describe] error describiendo imagen (intento {intento}/2): {e}", flush=True)
            if intento == 1:
                time.sleep(2)
    raise ultimo_error
