import os
import anthropic
from pathlib import Path

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(caption: str, comentarios_existentes: list[str], client_id: str | None = None) -> str:
    # Busca prompt específico del cliente, si no existe usa el default
    if client_id:
        client_prompt = _PROMPTS_DIR / "clients" / f"{client_id}.txt"
        if client_prompt.exists():
            template = client_prompt.read_text(encoding="utf-8")
        else:
            template = (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")
    else:
        template = (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")

    existentes_str = "\n".join(comentarios_existentes) if comentarios_existentes else "(sin comentarios)"
    return template.format(caption=caption, comentarios_existentes=existentes_str)


def generar_comentarios(caption: str, comentarios_existentes: list[str], client_id: str | None = None) -> list[str]:
    prompt = _load_prompt(caption, comentarios_existentes, client_id)

    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    comentarios = [line.strip() for line in raw.splitlines() if line.strip()]

    comentarios = comentarios[:60]
    if len(comentarios) < 60:
        raise ValueError(f"Se esperaban 60 comentarios, se obtuvieron {len(comentarios)}")

    return comentarios
