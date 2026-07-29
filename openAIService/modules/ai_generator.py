import os
import time
import anthropic
from pathlib import Path

# max_retries alto: el SDK reintenta solo los 429/529 (overloaded) al abrir el stream
_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=4)
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

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


def _load_template(client_id: str | None, account_id: int | None = None) -> str:
    """Trae el template del prompt: DB primero (prompt del cliente en su cuenta),
    si no hay, cae al .txt del cliente, y si tampoco, al default.txt.
    Para el cliente genérico el archivo es prompts/generico.txt."""
    if client_id == GENERIC_CLIENT_ID:
        # El genérico es UNO SOLO global: el mismo prompt para los posts sin
        # cliente de todos los vendedores (por eso no se filtra por cuenta).
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
        return (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")

    if client_id and _repo is not None:
        try:
            db_prompt = _repo.get_client_prompt(client_id, account_id)
            if db_prompt:
                return db_prompt
        except Exception as e:
            print(f"[ai] prompt DB no disponible, uso archivo ({e})", flush=True)

    if client_id:
        client_key = client_id.lower().replace(" ", "")
        client_prompt = _PROMPTS_DIR / "clients" / f"{client_key}.txt"
        if client_prompt.exists():
            return client_prompt.read_text(encoding="utf-8")

    return (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")


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


def _load_prompt(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, account_id: int | None = None, has_image: bool = False, client_gender=None) -> str:
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


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, image_b64: str = "", image_media_type: str = "", client_gender=None) -> list[str]:
    comentarios: list[str] = []
    for tipo, data in generar_comentarios_stream(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar, image_b64=image_b64, image_media_type=image_media_type, client_gender=client_gender):
        if tipo == "reset":
            comentarios = []          # la corrida anterior salió cortada: descartamos
        elif tipo == "comentario":
            comentarios.append(data)
    return comentarios


def generar_comentarios_stream(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None, image_b64: str = "", image_media_type: str = "", client_gender=None):
    """Yields (tipo, data): ("chunk", texto_parcial), ("comentario", linea_completa)
    o ("reset", None) cuando una generación salió cortada y se reintenta desde cero
    (el consumidor debe descartar lo emitido hasta ese punto).

    evitar: comentarios de tandas anteriores que el modelo no debe repetir ni
    parafrasear (usado por "Cargar más").
    image_b64/image_media_type: imagen del post (visión multimodal). Si viene, se
    manda como bloque de imagen a Claude junto con el prompt."""
    has_image = bool(image_b64)
    prompt = _load_prompt(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar, has_image=has_image, client_gender=client_gender)

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
                model="claude-opus-4-8",
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
                extra_body={
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
        if count >= _MIN_COMENTARIOS or intento == _MAX_INTENTOS:
            return
        prev_motivo = f"generación cortada ({count} líneas)"


def describir_imagen(image_b64: str, image_media_type: str = "", caption: str = "") -> str:
    """Describe textualmente la imagen de un post (para mostrarla al usuario como
    si fuera el pie de página). Llamada de visión corta, en español. Devuelve ""
    ante cualquier problema (el llamador simplemente no muestra descripción)."""
    if not image_b64:
        return ""
    content = [
        {"type": "image", "source": {"type": "base64",
                                      "media_type": image_media_type or "image/jpeg",
                                      "data": image_b64}},
        {"type": "text", "text": (
            "Describí en español, en 2 a 5 oraciones, qué se ve en esta imagen de "
            "Instagram (puede ser una foto, o la portada/preview de un video): "
            "personas y gestos, ropa y accesorios, lugar, objetos, comida, "
            "cualquier texto que aparezca en la imagen, y el ambiente general. "
            "Concreto y fiel a lo que se ve. Devolvé SOLO la descripción, sin "
            "preámbulos ni comillas."
        )},
    ]
    # Un 529/overloaded puntual dejaba al post sin descripción. Reintentamos una
    # vez antes de rendirnos. Si igual falla, PROPAGAMOS la excepción para que el
    # llamador sepa POR QUÉ (saturación/sin crédito) y se lo pueda avisar al
    # vendedor, en vez de quedar en silencio con la descripción vacía.
    ultimo_error = None
    for intento in (1, 2):
        try:
            resp = _client.messages.create(
                model="claude-opus-4-8",
                max_tokens=500,
                messages=[{"role": "user", "content": content}],
            )
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception as e:
            ultimo_error = e
            print(f"[describe] error describiendo imagen (intento {intento}/2): {e}", flush=True)
            if intento == 1:
                time.sleep(2)
    raise ultimo_error
