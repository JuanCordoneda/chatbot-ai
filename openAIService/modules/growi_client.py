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
IDVENTA    = os.environ.get("GROWI_IDVENTA", "32600")  # id del cliente en el CRM


@dataclass
class GrowiResult:
    success: bool
    insertadas: int
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _normalizar_orden(o: dict, disponible: float) -> dict:
    """
    El frontend manda las órdenes con su forma "cruda" (redsocialId, productoNombre,
    link, cuando, fechaProgramada, etc.). El CRM espera otra forma de campos
    (redsocial_id, prod, url, cant_inicial, programado, fecha_programada, ...).
    Si la orden ya viene en forma de CRM (tiene "url"), se respeta tal cual.
    """
    if "url" in o:
        return o

    cantidad = o.get("cantidad", 0)
    cuando = o.get("cuando", "ahora")
    programado = 1 if cuando not in ("ahora", None) else 0

    return {
        "redsocial_id": o.get("redsocialId") or o.get("redsocial_id"),
        "redsocial":    o.get("redsocial"),
        "prod":         o.get("productoNombre") or o.get("prod"),
        "demora":       " - ",
        "url":          o.get("link") or o.get("url") or "",
        "costo":        o.get("costo") or 0,
        "obs":          o.get("obs", ""),
        "cant_inicial": str(cantidad),
        "cantidad":     str(cantidad),
        "programado":   programado,
        "fecha_programada": o.get("fechaProgramada") or None,
        "comentarios":  [],
        "disponible":   disponible,
    }


def ejecutar_campana(post_url: str, comentarios: list[str],
                     ordenes: list[dict], disponible: float) -> GrowiResult:
    """
    Envía las órdenes al CRM. post_url y comentarios se usan solo para el
    informe; las ordenes se normalizan a la forma que espera enviar_trafico.php.
    """
    if not PHPSESSID or not IDVENDEDOR:
        raise NotImplementedError(
            "Growi no configurado. Agregar GROWI_CRM_PHPSESSID y GROWI_IDVENDEDOR al .env"
        )

    ordenes = [_normalizar_orden(o, disponible) for o in ordenes]

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
