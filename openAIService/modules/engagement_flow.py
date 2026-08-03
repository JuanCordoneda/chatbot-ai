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


# El esquema y el @usuario del medio son opcionales: Instagram comparte tanto
# instagram.com/p/CODE/ como instagram.com/usuario/reel/CODE/, y la gente pega
# el link pelado (sin https://). Las tres formas tienen que entrar.
INSTAGRAM_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/(?:[A-Za-z0-9._]+/)?(?:p|reels?|tv)/[A-Za-z0-9_-]+"
)

_CLIENTS_MAP_PATH = os.path.join(os.path.dirname(__file__), "..", "clients_map.json")

# La capa de datos multi-tenant es opcional: si el paquete `common` o la DB no
# están disponibles, todo cae al clients_map.json de siempre.
try:
    from common import repository as _repo
except Exception:
    _repo = None


def _load_clients_map() -> dict:
    try:
        with open(_CLIENTS_MAP_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        # Antes fallaba en silencio: un JSON corrupto o el archivo ausente dejaban
        # el mapa vacío y TODOS los posts caían al prompt de fallback sin que nada
        # lo avisara. Ahora queda registrado.
        print(f"[client] no pude leer {_CLIENTS_MAP_PATH}: {e}", flush=True)
        return {}

# Collabs: hay posts que un cliente publica desde una cuenta partner y que deben
# usar el prompt/género del cliente principal. Ej: muchos reels de Peter Fournier
# se postean desde @itshollyperez (su collab). Mapeamos el owner de la collab al
# ig_username del cliente principal ANTES de resolver prompt/género/nombre.
_CLIENT_ALIASES = {
    "itshollyperez": "peterjfournier",
}


def alias_owner(owner_username: str | None) -> str | None:
    """Si el owner es una cuenta de collab conocida, devuelve el ig_username del
    cliente principal; si no, lo devuelve tal cual. Idempotente."""
    if not owner_username:
        return owner_username
    return _CLIENT_ALIASES.get(owner_username.strip().lower(), owner_username)


def detectar_cliente(owner_username: str, account_id: int | None = None) -> str | None:
    """Devuelve el nombre para mostrar del cliente dueño del post.
    DB primero (multi-tenant); si no hay DB o no está el cliente, cae al
    clients_map.json. account_id acota la búsqueda a una cuenta cuando se conoce."""
    owner_username = alias_owner(owner_username)  # collab -> cliente principal
    if not owner_username:
        return None
    if _repo is not None:
        try:
            if _repo.db_available():
                # Con DB disponible, su respuesta es la verdad: si el cliente no
                # existe o está PAUSADO devuelve None y NO caemos al archivo. Si
                # cayéramos, "Pausar" no tendría ningún efecto (el cliente seguiría
                # detectándose desde clients_map.json).
                return _repo.get_client_display_name(owner_username, account_id)
        except Exception as e:
            print(f"[client] DB no disponible, uso clients_map.json ({e})", flush=True)
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

    client_gender = None
    client_quality = None
    if _repo is not None and post_data.owner_username:
        try:
            row = _repo.get_client_by_ig_username(post_data.owner_username)
            if row:
                client_gender = row.get("gender")
                client_quality = row.get("quality")
        except Exception:
            pass

    try:
        comentarios = generar_comentarios(
            post_data.caption, post_data.comments, client_id,
            transcription=post_data.transcription,
            photo_description=post_data.photo_description,
            is_video=post_data.is_video,
            image_b64=post_data.image_b64,
            image_media_type=post_data.image_media_type,
            client_gender=client_gender,
            client_quality=client_quality,
            n_imagenes=post_data.n_imagenes,
        )
    except Exception as e:
        return f"Error generando los comentarios con IA: {e}"

    resultado = None
    error_growi = None

    if GROWI_AVAILABLE:
        try:
            resultado = ejecutar_campana(post_url, comentarios)
        except NotImplementedError as e:
            error_growi = str(e)
        except Exception as e:
            error_growi = f"Error en Growi: {e}"
    else:
        error_growi = "Growi pendiente de configuración"

    return generar_informe(post_url, comentarios, resultado, error=error_growi if not resultado else None)
