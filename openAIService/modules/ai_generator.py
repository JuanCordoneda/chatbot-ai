import os
import anthropic
from pathlib import Path

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
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


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False) -> list[str]:
    return [data for tipo, data in generar_comentarios_stream(caption, comentarios_existentes, client_id, transcription, photo_description, is_video) if tipo == "comentario"]


def generar_comentarios_stream(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "", is_video: bool = False):
    """Yields (tipo, data): ("chunk", texto_parcial) o ("comentario", linea_completa)."""
    prompt = _load_prompt(caption, comentarios_existentes, client_id, transcription, photo_description, is_video)

    buffer = ""
    with _client.messages.stream(
        model="claude-haiku-4-5-20251001",
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
                    yield ("comentario", line)

    if buffer.strip():
        yield ("comentario", buffer.strip())
