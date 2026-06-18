import re
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


def es_link_instagram(text: str) -> bool:
    return bool(INSTAGRAM_URL_RE.search(text))


def extraer_link(text: str) -> str | None:
    match = INSTAGRAM_URL_RE.search(text)
    return match.group(0) if match else None


def procesar_post(post_url: str, client_id: str | None = None) -> str:
    """
    Flujo completo: scraping → generación IA → Growi → informe.
    client_id: nombre del archivo en prompts/clients/ (sin .txt), ej: "peter_fournier"
    """
    try:
        post_data = scrape_post(post_url)
    except Exception as e:
        return f"No pude acceder al post. Asegurate de que el link sea público. ({e})"

    try:
        comentarios = generar_comentarios(post_data.caption, post_data.comments, client_id)
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
