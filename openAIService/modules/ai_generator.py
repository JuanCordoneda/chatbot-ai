import os
import re
import time
import anthropic
from pathlib import Path

# Detección de la línea marcadora de sección de género, tolerante a variantes del
# modelo (mayúsculas, markdown, espacios, inglés). Espeja generoDeHeader() del
# front. Se usa para el safeguard de "cliente mixto salió con un solo género".
_HEADERS_GENERO = {
    "mujeres": "mujeres", "mujer": "mujeres", "women": "mujeres", "female": "mujeres",
    "hombres": "hombres", "hombre": "hombres", "men": "hombres", "male": "hombres",
}
_HEADER_RE = re.compile(r"^([a-zñáéíóú]+)\s*:$")


def _genero_de_header(line: str):
    s = (line or "").strip().lower()
    s = re.sub(r"^[*_#>`~\s-]+", "", s)
    s = re.sub(r"[*_`~\s]+$", "", s)
    m = _HEADER_RE.match(s)
    return _HEADERS_GENERO.get(m.group(1)) if m else None

# max_retries alto: el SDK reintenta solo los 429/529 (overloaded) al abrir el stream
_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=4)
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None) -> str:
    if client_id:
        client_key = client_id.lower().replace(" ", "")
        client_prompt = _PROMPTS_DIR / "clients" / f"{client_key}.txt"
        if client_prompt.exists():
            template = client_prompt.read_text(encoding="utf-8")
        else:
            template = (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")
    else:
        template = (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")

    if not is_video:
        template = template.replace("un reel de Instagram", "un post/foto de Instagram")
        template = template.replace("El reel dice", "El post dice")

    existentes_str = "\n".join(comentarios_existentes) if comentarios_existentes else "(sin comentarios)"
    prompt = template.format(caption=caption, comentarios_existentes=existentes_str)

    if photo_description:
        prompt += f"\n\nDescripción visual de la foto/imagen:\n---\n{photo_description}\n---"

    if transcription:
        prompt += f"\n\nTranscripción del audio del video:\n---\n{transcription}\n---"

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

    return prompt


# Si una generación devuelve menos de esto, asumimos que el stream se cortó
# (throttling / corte prematuro de la API) y reintentamos desde cero.
_MIN_COMENTARIOS = 20
_MAX_INTENTOS = 3


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None) -> list[str]:
    comentarios: list[str] = []
    for tipo, data in generar_comentarios_stream(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar):
        if tipo == "reset":
            comentarios = []          # la corrida anterior salió cortada: descartamos
        elif tipo == "comentario":
            comentarios.append(data)
    return comentarios


def generar_comentarios_stream(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False, evitar: list[str] | None = None):
    """Yields (tipo, data): ("chunk", texto_parcial), ("comentario", linea_completa)
    o ("reset", None) cuando una generación salió cortada y se reintenta desde cero
    (el consumidor debe descartar lo emitido hasta ese punto).

    evitar: comentarios de tandas anteriores que el modelo no debe repetir ni
    parafrasear (usado por "Cargar más")."""
    prompt = _load_prompt(caption, comentarios_existentes, client_id, transcription, photo_description, is_video, evitar)

    # Si el prompt pide las dos secciones de género, esperamos salida mixta. Si el
    # modelo emite una sola (bug intermitente: se salta "mujeres:" y todo cae en
    # "hombres"), lo tratamos como generación incompleta y reintentamos.
    espera_mixta = ("mujeres:" in prompt) and ("hombres:" in prompt)

    prev_motivo = None
    for intento in range(1, _MAX_INTENTOS + 1):
        if intento > 1:
            print(f"[ai] {prev_motivo}, reintento {intento}/{_MAX_INTENTOS}", flush=True)
            yield ("reset", None)
            time.sleep(3 * (intento - 1))  # backoff: 3s, 6s (overloaded suele ser transitorio)

        count = 0
        buffer = ""
        generos_vistos = set()
        try:
            with _client.messages.stream(
                model="claude-sonnet-5",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    buffer += text
                    yield ("chunk", text)
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        line = line.strip()
                        if line:
                            g = _genero_de_header(line)
                            if g:
                                generos_vistos.add(g)
                            count += 1
                            yield ("comentario", line)
        except Exception as e:
            # overloaded_error y otros transitorios de la API: reintentar desde cero
            if intento == _MAX_INTENTOS:
                raise
            prev_motivo = f"error de API ({e})"
            continue

        if buffer.strip():
            line = buffer.strip()
            g = _genero_de_header(line)
            if g:
                generos_vistos.add(g)
            count += 1
            yield ("comentario", line)

        # Damos la generación por buena si tiene suficientes comentarios y —para
        # clientes mixtos— aparecieron las dos secciones de género. Si no, y quedan
        # intentos, reintentamos desde cero.
        genero_ok = (not espera_mixta) or (generos_vistos >= {"mujeres", "hombres"})
        if (count >= _MIN_COMENTARIOS and genero_ok) or intento == _MAX_INTENTOS:
            return
        if count < _MIN_COMENTARIOS:
            prev_motivo = f"generación cortada ({count} líneas)"
        else:
            prev_motivo = f"género incompleto (secciones vistas: {sorted(generos_vistos) or 'ninguna'})"
