"""
Growi CRM client — envía las órdenes ya armadas por el frontend a enviar_trafico.php.
"""
import os
import json
import random
import time
import requests
from dataclasses import dataclass, field
from datetime import date

CRM_URL    = os.environ.get("GROWI_CRM_URL", "https://crm.growiagency.com")
EMAIL      = os.environ.get("GROWI_CRM_EMAIL", "")
PASSWORD   = os.environ.get("GROWI_CRM_PASSWORD", "")
IDVENDEDOR = os.environ.get("GROWI_IDVENDEDOR", "")
IDVENTA    = os.environ.get("GROWI_IDVENTA", "32600")  # id del cliente en el CRM

# El CRM ata la sesión a la IP que se loguea. Railway no da IP de salida fija,
# por eso todo el tráfico hacia el CRM se rutea por un proxy de IP fija si
# se configura GROWI_HTTP_PROXY (ej: http://user:pass@host:port).
_PROXY_URL = os.environ.get("GROWI_HTTP_PROXY", "")
_PROXIES = {"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else None

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

_session: requests.Session | None = None

# Timeouts (conectar, leer). El de conexión es corto a propósito: si el proxy de
# IP fija está caído, el connect se cuelga hasta agotarlo y ahí recién falla.
# Con 30s y 4 reintentos eso eran 2 minutos de espera para un error inevitable.
_TIMEOUT = (5, 30)


class GrowiUnavailable(RuntimeError):
    """No pudimos *llegar* al CRM (proxy caído, red, DNS). Es distinto de que el
    CRM nos rechace: acá no hay nada que reintentar en el momento ni credencial
    que revisar, y sobre todo NO tiene sentido generar comentarios que después
    no vamos a poder mandar."""


def _es_error_de_red(e: Exception) -> bool:
    """True si la excepción es 'no llegamos al server' y no 'el server dijo que
    no'. ProxyError y ConnectTimeout son subclases de ConnectionError, así que
    con el padre alcanza; Timeout cubre el read timeout."""
    return isinstance(e, (requests.exceptions.ConnectionError,
                          requests.exceptions.Timeout))


def _sin_ruta(e: Exception) -> GrowiUnavailable:
    """Mensaje de red uniforme, sin volcarle al vendedor el traceback con IPs y
    puertos internos (que es lo que se veía en el informe: 'ProxyError ...
    13.37.44.57:8888')."""
    destino = "el proxy de salida" if _PROXY_URL else "el CRM"
    print(f"[growi] sin ruta hacia el CRM via {destino}: {e!r}", flush=True)
    return GrowiUnavailable(
        "No se puede conectar con el CRM de Growi en este momento. "
        "Es un problema de conexión, no de tus datos: probá de nuevo en unos minutos."
    )


def _login() -> requests.Session:
    """
    Inicia sesión contra el CRM con usuario/contraseña. La sesión queda atada
    a la IP/user-agent desde la que se loguea, por eso no sirve copiar cookies
    del navegador: hay que loguearse desde el propio servidor.
    """
    session = requests.Session()
    session.headers.update({"user-agent": _USER_AGENT})
    if _PROXIES:
        session.proxies.update(_PROXIES)

    try:
        session.post(
            f"{CRM_URL}/cuenta/login.php",
            data={"correo": EMAIL, "password": PASSWORD},
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "referer": f"{CRM_URL}/cuenta/login.php",
                "origin": CRM_URL,
            },
            timeout=_TIMEOUT,
        )
        check = session.get(f"{CRM_URL}/paginas/trafico.php",
                            allow_redirects=False, timeout=_TIMEOUT)
    except Exception as e:
        if _es_error_de_red(e):
            raise _sin_ruta(e) from e
        raise

    if check.status_code != 200:
        raise NotImplementedError(
            "Login a Growi falló. Revisar GROWI_CRM_EMAIL / GROWI_CRM_PASSWORD."
        )

    return session


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        if not EMAIL or not PASSWORD:
            raise NotImplementedError(
                "Growi no configurado. Agregar GROWI_CRM_EMAIL y GROWI_CRM_PASSWORD al .env"
            )
        _session = _login()
    return _session


# Preflight: resultado cacheado para no pagar un round-trip al CRM en cada
# generación. El OK vale un rato largo (si anda, va a seguir andando); la falla
# vence rápido para que apenas vuelva el proxy se pueda trabajar de nuevo.
_PREFLIGHT_OK_TTL = 120.0
_PREFLIGHT_FAIL_TTL = 15.0
_preflight_cache: tuple[float, Exception | None] | None = None


def verificar_disponible() -> None:
    """Chequea que haya ruta hasta el CRM ANTES de generar comentarios.

    Sin esto el orden era: scrapear el post, gastar los tokens de la IA, mostrarle
    los 89 comentarios al vendedor y recién al publicar descubrir que el proxy
    estaba muerto — trabajo y plata tirados. Cuesta ~1s y no valida credenciales
    a propósito: solo responde "¿existe camino hasta el CRM?".

    Lanza GrowiUnavailable si no hay ruta. No lanza nada si Growi no está
    configurado: eso es una condición distinta, y la maneja quien publica.
    """
    global _preflight_cache

    if not EMAIL or not PASSWORD or not IDVENDEDOR:
        return  # sin configurar: no hay nada que chequear todavía

    ahora = time.monotonic()
    if _preflight_cache is not None:
        visto, fallo = _preflight_cache
        ttl = _PREFLIGHT_FAIL_TTL if fallo else _PREFLIGHT_OK_TTL
        if ahora - visto < ttl:
            if fallo:
                raise fallo
            return

    proxies = _PROXIES or {}
    try:
        requests.get(f"{CRM_URL}/cuenta/login.php", proxies=proxies,
                     headers={"user-agent": _USER_AGENT},
                     allow_redirects=False, timeout=_TIMEOUT)
    except Exception as e:
        if _es_error_de_red(e):
            fallo = _sin_ruta(e)
            _preflight_cache = (ahora, fallo)
            raise fallo from e
        # Cualquier otra cosa (un 500 del CRM, TLS raro) no es "no hay ruta":
        # dejamos seguir y que falle donde corresponde, con su propio mensaje.
        print(f"[growi] preflight no concluyente: {e!r}", flush=True)
        return

    _preflight_cache = (ahora, None)


# Encabezados que el CRM usa para saber el género de cada bloque de comentarios.
# La IA los emite como líneas sueltas dentro de la lista (ej: "mujeres:", "hombres:").
_HEADERS_GENERO = {"mujeres:", "hombres:"}


def _es_header_genero(linea: str) -> bool:
    return linea.strip().lower() in _HEADERS_GENERO


def _mezclar_comentarios(comentarios: list[str]) -> list[str]:
    """
    Mezcla los comentarios seleccionados antes de enviarlos para que no se
    publiquen siempre en el orden en que la IA los generó.

    Si vienen segmentados por género (líneas "mujeres:" / "hombres:"), respeta
    esos encabezados en su lugar y solo baraja los comentarios dentro de cada
    sección; así el CRM sigue percibiendo qué comentarios son de cada género.
    Si no hay encabezados, baraja toda la lista como antes.
    """
    if not any(_es_header_genero(c) for c in comentarios):
        mezclados = list(comentarios)
        random.shuffle(mezclados)
        return mezclados

    resultado: list[str] = []
    grupo: list[str] = []

    def _volcar_grupo():
        random.shuffle(grupo)
        resultado.extend(grupo)
        grupo.clear()

    for c in comentarios:
        if _es_header_genero(c):
            _volcar_grupo()       # cerramos la sección anterior ya barajada
            resultado.append(c)   # el encabezado queda fijo
        else:
            grupo.append(c)
    _volcar_grupo()               # última sección

    return resultado


def _log_comentarios_debug(nombre: str, coms: list[str]) -> None:
    """Log de debug: muestra cómo quedó la lista de una orden de comentarios."""
    headers = [c for c in coms if _es_header_genero(c)]
    if not headers:
        caso = "sin genero (lista plana)"
    elif len(headers) == 1:
        caso = f"solo {headers[0].strip().lower().rstrip(':')}"
    else:
        caso = "mixto (" + " + ".join(h.strip().lower().rstrip(':') for h in headers) + ")"
    print(f"[growi][debug] orden '{nombre}': caso {caso} | {len(coms)} lineas", flush=True)
    for i, c in enumerate(coms):
        marca = "  >>" if _es_header_genero(c) else f"  {i:>3}"
        print(f"[growi][debug]{marca} {c}", flush=True)


@dataclass
class GrowiResult:
    success: bool
    insertadas: int
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _normalizar_orden(o: dict, disponible: float, comentarios: list[str]) -> dict:
    """
    El frontend manda las órdenes con su forma "cruda" (redsocialId, productoNombre,
    link, cuando, fechaProgramada, etc.). El CRM espera otra forma de campos
    (redsocial_id, prod, url, cant_inicial, programado, fecha_programada, ...).
    Si la orden ya viene en forma de CRM (tiene "url"), se respeta tal cual salvo
    que le falten los textos de los comentarios generados por IA.

    Cada orden de comentarios usa SU propia lista si la trae (caso verificados +
    no verificados, que son dos órdenes con distintos textos); si no la trae, cae
    a la lista global `comentarios`. En ambos casos se mezcla respetando los
    encabezados de género.
    """
    def _coms_de(orden):
        base = orden.get("comentarios") or comentarios
        return _mezclar_comentarios(base)

    if "url" in o:
        if o.get("tipo") == "comentarios":
            o = {**o, "comentarios": _coms_de(o)}
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
        "comentarios":  _coms_de(o) if o.get("tipo") == "comentarios" else [],
        "disponible":   disponible,
    }


def ejecutar_campana(post_url: str, comentarios: list[str],
                     ordenes: list[dict], disponible: float) -> GrowiResult:
    """
    Envía las órdenes al CRM. post_url y comentarios se usan solo para el
    informe; las ordenes se normalizan a la forma que espera enviar_trafico.php.
    """
    if not IDVENDEDOR:
        raise NotImplementedError(
            "Growi no configurado. Agregar GROWI_IDVENDEDOR al .env"
        )

    session = _get_session()

    # Cada orden de comentarios se mezcla por dentro (respetando headers de
    # género) dentro de _normalizar_orden, usando su propia lista o la global.
    ordenes = [_normalizar_orden(o, disponible, comentarios) for o in ordenes]

    # --- DEBUG: cómo quedó la lista de cada orden de comentarios ---
    for o in ordenes:
        if o.get("comentarios"):
            _log_comentarios_debug(o.get("prod") or o.get("productoNombre") or "comentarios", o["comentarios"])
    # --- fin DEBUG ---

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

    request_headers = {
        "referer":          f"{CRM_URL}/paginas/trafico.php",
        "content-type":     "application/json; charset=UTF-8",
        "x-requested-with": "XMLHttpRequest",
    }

    # El CRM ata la sesión a la IP que se loguea y Railway rota la IP de salida
    # entre requests, asi que un 401 puede ser solo mala suerte de que el login
    # y el POST salieron por IPs distintas. Reintentamos con login fresco unas
    # cuantas veces para aumentar la chance de que coincidan (mitigación
    # temporal hasta tener un proxy de IP fija -> GROWI_HTTP_PROXY).
    global _session
    max_intentos = 4
    for intento in range(1, max_intentos + 1):
        try:
            resp = session.post(
                f"{CRM_URL}/paginas/enviar_trafico.php",
                json=payload,
                headers=request_headers,
                timeout=_TIMEOUT,
            )
        except Exception as e:
            # Un error de red NO se reintenta: los reintentos existen para el 401
            # (login y POST saliendo por IPs distintas). Si no hay ruta al CRM,
            # reintentar solo multiplica la espera por 4 antes del mismo error.
            if _es_error_de_red(e):
                raise _sin_ruta(e) from e
            raise
        if resp.status_code != 401:
            break
        print(f"[growi] 401 en intento {intento}/{max_intentos}, reintentando con login fresco", flush=True)
        _session = None
        if intento < max_intentos:
            session = _get_session()
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
