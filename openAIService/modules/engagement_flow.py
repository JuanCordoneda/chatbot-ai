import re
import json
import os
from modules.post_processor import scrape_post
from modules.ai_generator import generar_comentarios
from modules.reporter import generar_informe
try:
    from modules.growi_client import (ejecutar_campana, verificar_disponible,
                                      GrowiUnavailable)
    GROWI_AVAILABLE = True
except Exception:
    GROWI_AVAILABLE = False
    class GrowiUnavailable(RuntimeError):
        """Placeholder para que los `except` de abajo sigan siendo válidos
        cuando growi_client no se pudo importar."""


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


def resolver_cliente_del_post(owner_username: str | None,
                              collaborators: list | None = None,
                              account_id: int | None = None) -> tuple[str | None, str | None]:
    """Decide de qué cliente es un post, mirando el dueño Y los colaboradores.

    Devuelve (ig_username_del_cliente, nombre_para_mostrar). El primer valor es
    el @usuario con el que el resto del flujo pide prompt, rangos, género y
    CAMPAÑA: por eso tiene que ser el del cliente real, no el de quien publicó.

    Un post en colaboración lo publica una sola cuenta, pero es de las dos. Si el
    dueño no es cliente nuestro y un colaborador sí, el post es de ese cliente.
    Esto reemplaza al viejo `_CLIENT_ALIASES` hardcodeado, que solo servía para
    el caso que alguien se acordó de agregar a mano.

    Prioridad: el dueño primero, después los colaboradores en el orden en que
    los manda Instagram. `account_id` acota la búsqueda a TU cuenta: sin eso, el
    colaborador que es cliente de otro tenant se te asignaría a vos.

    Si no hay ningún cliente, devuelve (dueño, None) — el flujo sigue con el
    prompt genérico, igual que antes.
    """
    owner_ig = alias_owner(owner_username) if owner_username else None

    candidatos = []
    for u in [owner_username] + list(collaborators or []):
        c = alias_owner(u)
        if c and c.lower() not in [x.lower() for x in candidatos]:
            candidatos.append(c)

    encontrados = []
    for cand in candidatos:
        nombre = detectar_cliente(cand, account_id)
        if nombre:
            encontrados.append((cand, nombre))

    if not encontrados:
        return owner_ig, None

    # Más de un cliente nuestro en el mismo post (dueño y colaborador, o dos
    # colaboradores). Gana el dueño por orden, pero queda registrado: si la
    # elección no era la que el vendedor esperaba, el log dice contra qué otras
    # opciones se decidió.
    if len(encontrados) > 1:
        print(f"[client] post con varios clientes {[e[0] for e in encontrados]} — "
              f"me quedo con '{encontrados[0][0]}' (el dueño manda, después los collabs)",
              flush=True)

    return encontrados[0]


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
    # Mismo preflight que la vía web: si no hay ruta al CRM, no scrapeamos ni
    # generamos nada, porque el envío al final va a fallar igual.
    if GROWI_AVAILABLE:
        try:
            verificar_disponible()
        except GrowiUnavailable as e:
            return str(e)

    try:
        post_data = scrape_post(post_url)
    except Exception as e:
        return f"No pude acceder al post. Asegurate de que el link sea público. ({e})"

    # Mismo criterio que el panel: el post puede ser de un colaborador. El
    # @usuario que sale de acá es el del cliente real, y es el que se usa después
    # para el género/calidad y para la traza.
    owner_ig, cliente_detectado = resolver_cliente_del_post(
        post_data.owner_username, post_data.collaborators)
    if client_id is None:
        client_id = cliente_detectado

    client_gender = None
    client_quality = None
    if _repo is not None and owner_ig:
        try:
            row = _repo.get_client_by_ig_username(owner_ig)
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
            shortcode=post_data.shortcode,
        )
    except Exception as e:
        return f"Error generando los comentarios con IA: {e}"

    resultado = None
    error_growi = None

    if GROWI_AVAILABLE:
        from common.growi_trace import contexto as traza_contexto
        try:
            # origen="whatsapp": este flujo lo dispara el bot, no un vendedor
            # desde el panel. En la auditoría hay que poder distinguirlos.
            with traza_contexto(origen="whatsapp", post_url=post_url,
                                client_ig_username=owner_ig or post_data.owner_username or None):
                resultado = ejecutar_campana(post_url, comentarios)
        except GrowiUnavailable as e:
            error_growi = str(e)
        except NotImplementedError as e:
            error_growi = str(e)
        except Exception as e:
            error_growi = f"Error en Growi: {e}"
    else:
        error_growi = "Growi pendiente de configuración"

    return generar_informe(post_url, comentarios, resultado, error=error_growi if not resultado else None)
