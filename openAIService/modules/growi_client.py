"""
Growi API client — PENDIENTE de implementación.
Se activa una vez recibidas las credenciales y documentación de Growi.
"""
import os
from dataclasses import dataclass


GROWI_API_KEY = os.environ.get("GROWI_API_KEY", "")
GROWI_API_URL = os.environ.get("GROWI_API_URL", "https://api.growi.com")

LIKES_TOTAL = 5000
VIEWS_TOTAL = 5000
REPOSTS_TOTAL = 1000
SHARES_TOTAL = 1000
LOTES = 5
COMENTARIOS_TOTAL = 60


@dataclass
class GrowiResult:
    success: bool
    comentarios_enviados: int
    likes: int
    views: int
    reposts: int
    shares: int
    errores: list[str]


def ejecutar_campana(post_url: str, comentarios: list[str]) -> GrowiResult:
    """
    Ejecuta la campaña completa en Growi.
    PENDIENTE: reemplazar el stub por la implementación real cuando
    se tengan las credenciales y documentación de la API.
    """
    if not GROWI_API_KEY:
        raise NotImplementedError(
            "Growi no configurado. Agregar GROWI_API_KEY y GROWI_API_URL al .env"
        )

    # TODO: implementar cuando llegue la documentación de Growi
    # Estructura esperada:
    # 1. POST /auth → token
    # 2. POST /orders → crear orden con post_url
    # 3. POST /orders/{id}/comments → enviar los 20 comentarios
    # 4. POST /orders/{id}/actions → likes/views/reposts/shares en 5 lotes de 1000/hora
    # 5. GET /orders/{id} → estado final

    raise NotImplementedError("Growi client pendiente de implementación")
