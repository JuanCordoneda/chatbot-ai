import os
import anthropic
from pathlib import Path

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "") -> str:
    if client_id:
        client_prompt = _PROMPTS_DIR / "clients" / f"{client_id}.txt"
        if client_prompt.exists():
            template = client_prompt.read_text(encoding="utf-8")
        else:
            template = (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")
    else:
        template = (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")

    existentes_str = "\n".join(comentarios_existentes) if comentarios_existentes else "(sin comentarios)"
    prompt = template.format(caption=caption, comentarios_existentes=existentes_str)

    if photo_description:
        prompt += f"\n\nDescripción visual de la foto/imagen:\n---\n{photo_description}\n---"

    if transcription:
        prompt += f"\n\nTranscripción del audio del video:\n---\n{transcription}\n---"

    return prompt


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = "") -> list[str]:
    return list(generar_comentarios_stream(caption, comentarios_existentes, client_id, transcription, photo_description))


def generar_comentarios_stream(caption: str, comentarios_existentes: list[str], client_id: str | None = None, transcription: str = "", photo_description: str = ""):
    prompt = _load_prompt(caption, comentarios_existentes, client_id, transcription, photo_description)

    buffer = ""
    with _client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            buffer += text
            lines = buffer.split("\n")
            buffer = lines.pop()
            for line in lines:
                line = line.strip()
                if line:
                    yield line

    if buffer.strip():
        yield buffer.strip()
