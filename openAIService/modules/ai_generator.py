import os
import time
import anthropic
from pathlib import Path

# max_retries alto: el SDK reintenta solo los 429/529 (overloaded) al abrir el stream
_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=4)
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False) -> str:
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

    return prompt


# Si una generación devuelve menos de esto, asumimos que el stream se cortó
# (throttling / corte prematuro de la API) y reintentamos desde cero.
_MIN_COMENTARIOS = 20
_MAX_INTENTOS = 3


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False) -> list[str]:
    comentarios: list[str] = []
    for tipo, data in generar_comentarios_stream(caption, comentarios_existentes, client_id, transcription, photo_description, is_video):
        if tipo == "reset":
            comentarios = []          # la corrida anterior salió cortada: descartamos
        elif tipo == "comentario":
            comentarios.append(data)
    return comentarios


def generar_comentarios_stream(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False):
    """Yields (tipo, data): ("chunk", texto_parcial), ("comentario", linea_completa)
    o ("reset", None) cuando una generación salió cortada y se reintenta desde cero
    (el consumidor debe descartar lo emitido hasta ese punto)."""
    prompt = _load_prompt(caption, comentarios_existentes, client_id, transcription, photo_description, is_video)

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
