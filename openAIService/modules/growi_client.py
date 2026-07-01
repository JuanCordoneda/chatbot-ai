"""
Growi CRM client — envía las órdenes ya armadas por el frontend a enviar_trafico.php.
"""
import os
import json
import requests
from dataclasses import dataclass, field
from datetime import date

CRM_URL    = os.environ.get("GROWI_CRM_URL", "https://crm.growiagency.com")
PHPSESSID  = os.environ.get("GROWI_CRM_PHPSESSID", "")
REMEMBERME = os.environ.get("GROWI_CRM_REMEMBERME", "")
IDVENDEDOR = os.environ.get("GROWI_IDVENDEDOR", "")
IDVENTA    = os.environ.get("GROWI_IDVENTA", "1")


@dataclass
class GrowiResult:
    success: bool
    insertadas: int
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def ejecutar_campana(post_url: str, comentarios: list[str],
                     ordenes: list[dict], disponible: float) -> GrowiResult:
    """
    Envía las órdenes al CRM tal como vienen del frontend.
    post_url y comentarios se usan solo para el informe; las ordenes
    ya traen url, producto, cantidad, programado, etc.
    """
    if not PHPSESSID or not IDVENDEDOR:
        raise NotImplementedError(
            "Growi no configurado. Agregar GROWI_CRM_PHPSESSID y GROWI_IDVENDEDOR al .env"
        )

    costo_total = sum(float(o.get("costo", 0)) for o in ordenes)

    print(f"[growi] enviando {len(ordenes)} ordenes: {json.dumps(ordenes, ensure_ascii=False)}", flush=True)

    payload = {
        "idvendedor":   IDVENDEDOR,
        "idventa":      IDVENTA,
        "fecha":        date.today().isoformat(),
        "vendedor":     " ",
        "cant_enviada": 0,
        "aprobada":     "Aprobado",
        "ordenes":      ordenes,
        "creador":      IDVENDEDOR,
        "disponible":   disponible,
        "resto":        round(disponible - costo_total, 6),
        "costo_orden":  round(costo_total, 6),
    }

    resp = requests.post(
        f"{CRM_URL}/paginas/enviar_trafico.php",
        json=payload,
        cookies={"PHPSESSID": PHPSESSID, "rememberme": REMEMBERME},
        headers={
            "referer":          f"{CRM_URL}/paginas/trafico.php",
            "content-type":     "application/json; charset=UTF-8",
            "x-requested-with": "XMLHttpRequest",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    print(f"[growi] respuesta CRM: {json.dumps(data, ensure_ascii=False)}", flush=True)

    return GrowiResult(
        success=data.get("success", False),
        insertadas=data.get("insertadas", 0),
        messages=data.get("messages", []),
        warnings=data.get("warnings", []),
        errors=data.get("errors", []),
        raw=data,
    )
