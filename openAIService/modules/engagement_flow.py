import re
import json
import os
from modules.post_processor import scrape_post
from modules.ai_generator import generar_comentarios
from modules.reporter import generar_informe
try:
    from modules.growi_client import ejecutar_campana
    GROWI_AVAILABLE = True
except Exception:
    GROWI_AVAILABLE = False


INSTAGRAM_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+"
)

_CLIENTS_MAP_PATH = os.path.join(os.path.dirname(__file__), "..", "clients_map.json")

def _load_clients_map() -> dict:
    try:
        with open(_CLIENTS_MAP_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def detectar_cliente(owner_username: str) -> str | None:
    clients_map = _load_clients_map()
    return clients_map.get(owner_username)


def es_link_instagram(text: str) -> bool:
    return bool(INSTAGRAM_URL_RE.search(text))


def extraer_link(text: str) -> str | None:
    match = INSTAGRAM_URL_RE.search(text)
    return match.group(0) if match else None


def procesar_post(post_url: str, client_id: str | None = None) -> str:
    """
    Flujo completo: scraping → generación IA → Growi → informe.
    client_id: se puede pasar manualmente, o se auto-detecta desde el owner del post via clients_map.json
    """
    try:
        post_data = scrape_post(post_url)
    except Exception as e:
        return f"No pude acceder al post. Asegurate de que el link sea público. ({e})"

    if client_id is None and post_data.owner_username:
        client_id = detectar_cliente(post_data.owner_username)

    # TEMPORAL: generación de comentarios deshabilitada para debugging del flujo
    return (
        f"[DEBUG] Scraping OK.\n"
        f"owner: {post_data.owner_username}\n"
        f"client_id: {client_id}\n"
        f"is_video: {post_data.is_video}\n"
        f"caption ({len(post_data.caption)} chars): {post_data.caption[:200]}\n"
        f"transcription ({len(post_data.transcription)} chars): {post_data.transcription[:200] if post_data.transcription else '(vacía)'}\n"
        f"photo_description: {post_data.photo_description[:200] if post_data.photo_description else '(vacía)'}"
    )
