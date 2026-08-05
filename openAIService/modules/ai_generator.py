import os
import time
import anthropic
from pathlib import Path

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


def _client_layer(client_id: str, account_id: int | None) -> str:
    """La capa propia del cliente: DB primero, si no el .txt de la imagen."""
    if _repo is not None:
        try:
            db_prompt = _repo.get_client_prompt(client_id, account_id)
            if db_prompt:
                return db_prompt
        except Exception as e:
            print(f"[ai] prompt DB no disponible, uso archivo ({e})", flush=True)
    client_key = client_id.lower().replace(" ", "")
    client_prompt = _PROMPTS_DIR / "clients" / f"{client_key}.txt"
    if client_prompt.exists():
        return client_prompt.read_text(encoding="utf-8")
    return ""


def _load_template(client_id: str | None, account_id: int | None = None) -> str:
    """Arma el prompt final: base genérica + capa del cliente (ver arriba).

    Sin cliente (o cliente genérico) va solo la base. Si el cliente no tiene
    prompt propio, también va solo la base: es mejor default que el default.txt,
    que quedó como último recurso por si no hay genérico cargado."""
    base = _generic_base()

    if not client_id or client_id == GENERIC_CLIENT_ID:
        return base or (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")

    especifico = (_client_layer(client_id, account_id) or "").strip()
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


def _system_output_format(client_gender) -> str:
    if client_gender == "male":
        return (
            "\n\nFORMATO DE SALIDA (obligatorio):\n"
            "- Generá ~70 comentarios en total, uno por línea, listos para publicar.\n"
            "- La primera línea debe ser EXACTAMENTE:\nhombres:\n"
            "- Debajo, todos los comentarios (todos escritos por hombres).\n"
            "- No incluyas el encabezado \"mujeres:\".\n" + _OUT_BASE_NO
        )
    if client_gender == "female":
        return (
            "\n\nFORMATO DE SALIDA (obligatorio):\n"
            "- Generá ~70 comentarios en total, uno por línea, listos para publicar.\n"
            "- La primera línea debe ser EXACTAMENTE:\nmujeres:\n"
            "- Debajo, todos los comentarios (todos escritos por mujeres).\n"
            "- No incluyas el encabezado \"hombres:\".\n" + _OUT_BASE_NO
        )
    # mixto (None u otro)
    return (
        "\n\nFORMATO DE SALIDA (obligatorio):\n"
        "- Generá ~70 comentarios en total, completamente mezclados dentro de cada sección.\n"
        "- Primero la línea EXACTAMENTE:\nmujeres:\n"
        "seguida de ~la mitad de los comentarios, escritos por mujeres, uno por línea.\n"
        "- Después la línea EXACTAMENTE:\nhombres:\n"
        "seguida de la otra mitad, escritos por hombres, uno por línea.\n"
        "- Los encabezados \"mujeres:\" y \"hombres:\" van tal cual, en su propia línea.\n" + _OUT_BASE_NO
    )


def _load_prompt(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, account_id: int | None = None, has_image: bool = False, client_gender=None, n_imagenes: int = 1, keyword: str = "") -> str:
    # Modo keyword: camino aparte y completo (ver KEYWORD_CLIENT_ID). Nada del
    # contexto del post entra acá.
    if keyword.strip():
        return _keyword_prompt(keyword, evitar)

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
    prompt = (template
              .replace("{caption}", caption)
              .replace("{comentarios_existentes}", existentes_str))

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
    if evitar:
        evitar_str = "\n".join(f"- {c}" for c in evitar)
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
    # No es editable desde el panel. Solo se agrega si el prompt no lo trae ya
    # embebido (prompts viejos con "FORMATO DE SALIDA" adentro siguen funcionando).
    if "formato de salida" not in template.lower():
        prompt += _system_output_format(client_gender)

    return prompt


# Si una generación devuelve menos de esto, asumimos que el stream se cortó
# (throttling / corte prematuro de la API) y reintentamos desde cero.
_MIN_COMENTARIOS = 20
_MAX_INTENTOS = 3


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, image_b64: str = "", image_media_type: str = "", client_gender=None, client_quality=None, n_imagenes: int = 1, keyword: str = "") -> list[str]:
    comentarios: list[str] = []
    for tipo, data in generar_comentarios_stream(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar, image_b64=image_b64, image_media_type=image_media_type, client_gender=client_gender, client_quality=client_quality, n_imagenes=n_imagenes, keyword=keyword):
        if tipo == "reset":
            comentarios = []          # la corrida anterior salió cortada: descartamos
        elif tipo == "comentario":
            comentarios.append(data)
    return comentarios


def generar_comentarios_stream(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, image_b64: str = "", image_media_type: str = "", client_gender=None, client_quality=None, n_imagenes: int = 1, keyword: str = ""):
    """Yields (tipo, data): ("chunk", texto_parcial), ("comentario", linea_completa)
    o ("reset", None) cuando una generación salió cortada y se reintenta desde cero
    (el consumidor debe descartar lo emitido hasta ese punto).

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
    # escribir una palabra y se paga igual.
    has_image = bool(image_b64) and not modo_keyword
    # Escribir la misma palabra 40 veces no mejora con el modelo caro.
    modelo = _MODEL_STANDARD if modo_keyword else _modelo(client_quality)
    print(f"[ai] calidad={client_quality or 'standard'} modelo={modelo}"
          + (f" keyword={keyword!r}" if modo_keyword else ""), flush=True)
    prompt = _load_prompt(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar, has_image=has_image, client_gender=client_gender, n_imagenes=n_imagenes, keyword=keyword)

    # El piso de "generación cortada" es relativo a lo que se pidió: con 40
    # comentarios de una palabra, el fijo de 70 no aplica.
    minimo = max(1, int(KEYWORD_CANTIDAD * 0.75)) if modo_keyword else _MIN_COMENTARIOS

    if has_image:
        content = [
            {"type": "image", "source": {"type": "base64",
                                          "media_type": image_media_type or "image/jpeg",
                                          "data": image_b64}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt

    prev_motivo = None
    for intento in range(1, _MAX_INTENTOS + 1):
        if intento > 1:
            print(f"[ai] {prev_motivo}, reintento {intento}/{_MAX_INTENTOS}", flush=True)
            yield ("reset", None)
            time.sleep(3 * (intento - 1))  # backoff: 3s, 6s (overloaded suele ser transitorio)

        count = 0
        buffer = ""
        try:
            with _client.messages.stream(
                model=modelo,
                # max_tokens subido: con thinking prendido, el "pensar" también
                # consume de este cupo; con 4096 podría cortar la tanda de ~70.
                max_tokens=8192,
                messages=[{"role": "user", "content": content}],
                # EXPERIMENTO (adaptive thinking): que el modelo planee la
                # distribución de largos/voces y evite repetir ANTES de escribir,
                # para bajar el "bot-feel". effort=medium acota cuánto piensa así
                # no penaliza tanto la latencia del streaming en vivo.
                # Va por extra_body porque el SDK pineado (anthropic 0.54.0) no
                # expone estos kwargs; extra_body los inyecta en el body del request.
                # Revertir = borrar este extra_body y volver max_tokens=4096.
                # En modo keyword no hay nada que planear (es la misma palabra N
                # veces): pensar solo suma latencia y tokens.
                extra_body={} if modo_keyword else {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "medium"},
                },
            ) as stream:
                for text in stream.text_stream:
                    buffer += text
                    yield ("chunk", text)
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        line = line.strip()
                        if line:
                            count += 1
                            yield ("comentario", line)
        except Exception as e:
            # overloaded_error y otros transitorios de la API: reintentar desde cero
            if intento == _MAX_INTENTOS:
                raise
            prev_motivo = f"error de API ({e})"
            continue

        if buffer.strip():
            count += 1
            yield ("comentario", buffer.strip())

        # generación completa (o último intento): la damos por buena
        if count >= minimo or intento == _MAX_INTENTOS:
            return
        prev_motivo = f"generación cortada ({count} líneas)"


def describir_imagen(image_b64: str, image_media_type: str = "", caption: str = "",
                     n_imagenes: int = 1, es_video: bool = False) -> str:
    """Describe textualmente la imagen de un post (para mostrarla al usuario como
    si fuera el pie de página). Llamada de visión corta, en español. Devuelve ""
    ante cualquier problema (el llamador simplemente no muestra descripción)."""
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
                "Al final agregá una última línea que empiece con \"En conjunto:\" "
                "contando en 1 o 2 oraciones qué pasa en el video de principio a "
                "fin (cómo evoluciona la escena)."
            )
        else:
            qué_es = (
                f"Esta imagen es un mosaico con las {n_imagenes} fotos de un "
                "carrusel de Instagram, en orden"
            )
            unidad, conjunto = "foto", (
                "Al final agregá una última línea que empiece con \"En conjunto:\" "
                "resumiendo de qué se trata el carrusel en 1 oración."
            )
        instruccion = (
            f"{qué_es}, ordenadas de izquierda a derecha y de arriba hacia abajo, "
            "cada una con su número arriba a la izquierda (el número está "
            "sobreimpreso por nosotros, no forma parte de la imagen).\n"
            f"Describí en español CADA {unidad} por separado: {qué_mirar}.\n"
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
            "Describí en español, en 2 a 5 oraciones, qué se ve en esta imagen de "
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
                # la descripción a la mitad.
                max_tokens=500 + 150 * max(0, n_imagenes - 1),
                messages=[{"role": "user", "content": content}],
            )
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception as e:
            ultimo_error = e
            print(f"[describe] error describiendo imagen (intento {intento}/2): {e}", flush=True)
            if intento == 1:
                time.sleep(2)
    raise ultimo_error
