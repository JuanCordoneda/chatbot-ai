"""Lo que Anthropic nos FACTURÓ de verdad, para contrastarlo con lo que el panel
estima.

El panel calcula el costo en el momento de cada llamada, multiplicando tokens por
una tabla de precios que está en el código (ver ai_generator._PRECIOS). Eso sirve
para repartir el gasto entre vendedores —que es la pregunta del panel— pero es un
ESTIMADO: si una tarifa cambia y nadie toca la tabla, el número se va sin que
nadie se entere. Esta es la contraparte: el número real, salido de la factura.

Sale del Cost Report de la Admin API de Anthropic, que NO es la misma API con la
que se generan los comentarios: necesita una admin key aparte
(ANTHROPIC_ADMIN_KEY, empieza con sk-ant-admin). Sin esa key esto devuelve
"no configurado" y el panel muestra sólo el estimado: es un extra de control, no
puede ser motivo de que la pestaña deje de andar.
"""
import os
import threading
import time

import requests

_API = "https://api.anthropic.com/v1/organizations/cost_report"
_TIMEOUT = 20

# Los datos aparecen ~5 min después de cada llamada y el corte es por día, así
# que pedirlos seguido no aporta nada: la doc pide no pasar de una consulta por
# minuto. Cacheamos 10 minutos por rango.
_TTL = 600
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def disponible() -> bool:
    return bool(os.environ.get("ANTHROPIC_ADMIN_KEY", "").strip())


def _pedir_pagina(desde: str, hasta: str, page: str | None) -> dict:
    params = {
        "starting_at": desde,
        "ending_at": hasta,
        "bucket_width": "1d",
        # Agrupar por description es lo que hace que cada fila traiga el modelo y
        # el tipo de token; sin eso vuelve un total por día y no se puede saber
        # qué modelo se comió la plata.
        "group_by[]": "description",
        "limit": 31,
    }
    if page:
        params["page"] = page
    r = requests.get(
        _API, params=params, timeout=_TIMEOUT,
        headers={
            "anthropic-version": "2023-06-01",
            "x-api-key": os.environ["ANTHROPIC_ADMIN_KEY"].strip(),
            "User-Agent": "Crowi/1.0 (panel de tokens)",
        },
    )
    r.raise_for_status()
    return r.json()


def facturado(desde: str, hasta: str) -> dict:
    """Costo real facturado entre dos fechas (YYYY-MM-DD).

    `hasta` es EXCLUSIVO, igual que en el resto del panel de tokens, así que el
    llamador pasa el mismo rango que usa para el estimado y los dos números son
    comparables sin corregir nada.

    Devuelve {"disponible": bool, "total_usd": float, "por_dia": [...],
              "por_modelo": [...], "error": str|None}.
    """
    if not disponible():
        return {"disponible": False, "motivo": "sin ANTHROPIC_ADMIN_KEY"}

    clave = (desde, hasta)
    with _cache_lock:
        cacheado = _cache.get(clave)
        if cacheado and time.time() - cacheado[0] < _TTL:
            return cacheado[1]

    desde_iso = f"{desde}T00:00:00Z"
    hasta_iso = f"{hasta}T00:00:00Z"

    por_dia: dict[str, float] = {}
    por_modelo: dict[str, float] = {}
    total = 0.0
    try:
        page = None
        for _ in range(20):        # tope de páginas: 31 días por página alcanza
            data = _pedir_pagina(desde_iso, hasta_iso, page)
            for bucket in data.get("data") or []:
                dia = (bucket.get("starting_at") or "")[:10]
                for item in bucket.get("results") or []:
                    # OJO: `amount` viene en la unidad MÁS CHICA de la moneda
                    # (centavos) y como string. "123.45" son U$S 1.2345, no
                    # ciento veintitrés dólares. Sin el /100 el panel mostraría
                    # cien veces el gasto real.
                    try:
                        usd = float(item.get("amount") or 0) / 100.0
                    except (TypeError, ValueError):
                        continue
                    total += usd
                    if dia:
                        por_dia[dia] = por_dia.get(dia, 0.0) + usd
                    # Las líneas que no son de tokens (web search, code
                    # execution) vienen sin modelo: van juntas en "otros" en vez
                    # de perderse, así que el total del desglose cierra.
                    modelo = item.get("model") or item.get("cost_type") or "otros"
                    por_modelo[modelo] = por_modelo.get(modelo, 0.0) + usd
            if not data.get("has_more"):
                break
            page = data.get("next_page")
            if not page:
                break
    except requests.HTTPError as e:
        codigo = e.response.status_code if e.response is not None else 0
        # 401/403 es la causa más común y tiene una explicación concreta: la key
        # de generar comentarios NO sirve acá, hace falta una admin key.
        motivo = ("La ANTHROPIC_ADMIN_KEY no es válida o no tiene permiso de admin"
                  if codigo in (401, 403) else f"Anthropic respondió {codigo}")
        return {"disponible": True, "error": motivo}
    except Exception as e:
        print(f"[costos] no pude traer el facturado: {e!r}", flush=True)
        return {"disponible": True, "error": "No pude consultar la facturación"}

    resultado = {
        "disponible": True,
        "error": None,
        "total_usd": round(total, 4),
        "por_dia": [{"dia": d, "costo_usd": round(v, 4)} for d, v in sorted(por_dia.items())],
        "por_modelo": sorted(
            ({"clave": k, "costo_usd": round(v, 4)} for k, v in por_modelo.items()),
            key=lambda x: -x["costo_usd"]),
    }
    with _cache_lock:
        _cache[clave] = (time.time(), resultado)
    return resultado
