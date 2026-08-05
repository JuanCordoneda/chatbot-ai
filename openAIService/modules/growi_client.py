"""
Growi CRM client — envía las órdenes ya armadas por el frontend a enviar_trafico.php.
"""
import os
import json
import random
import threading
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
# por eso todo el tráfico hacia el CRM se rutea por un proxy de IP fija.
# GROWI_HTTP_PROXY acepta VARIOS separados por coma y se prueban en orden; con
# uno solo se comporta igual que antes. Ver common/proxy_pool.py.
from common.proxy_pool import ProxyPool, proxies_de, _ofuscar

_POOL = ProxyPool(os.environ.get("GROWI_HTTP_PROXY", ""))

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

# La sesión va atada al proxy por el que se logueó: las cookies del CRM valen
# solo para esa IP, así que cambiar de proxy obliga a relogearse.
_session: requests.Session | None = None
_session_proxy: str | None = None
_session_lock = threading.Lock()

# Timeouts (conectar, leer). El de conexión es corto a propósito: si el proxy de
# IP fija está caído, el connect se cuelga hasta agotarlo y ahí recién falla.
# Con 30s y 4 reintentos eso eran 2 minutos de espera para un error inevitable.
_TIMEOUT = (5, 30)


class GrowiUnavailable(RuntimeError):
    """No pudimos *llegar* al CRM (proxy caído, red, DNS). Es distinto de que el
    CRM nos rechace: acá no hay nada que reintentar en el momento ni credencial
    que revisar, y sobre todo NO tiene sentido generar comentarios que después
    no vamos a poder mandar.

    `reintentable` dice si el envío se puede encolar sin riesgo. False significa
    que el POST pudo haber llegado al CRM: reintentarlo duplicaría la orden y le
    cobraría dos veces al cliente, así que esa va a revisión manual.
    """

    def __init__(self, mensaje: str, reintentable: bool = True):
        super().__init__(mensaje)
        self.reintentable = reintentable


def _es_error_de_red(e: Exception) -> bool:
    """True si la excepción es 'no llegamos al server' y no 'el server dijo que
    no'. ProxyError y ConnectTimeout son subclases de ConnectionError, así que
    con el padre alcanza; Timeout cubre el read timeout."""
    return isinstance(e, (requests.exceptions.ConnectionError,
                          requests.exceptions.Timeout))


def _falló_al_conectar(e: Exception) -> bool:
    """True solo si la request NUNCA salió: no se pudo abrir el socket.

    La distinción es plata real. enviar_trafico.php NO es idempotente: si el
    POST llegó al CRM y lo que falló fue esperar la respuesta (ReadTimeout), la
    orden puede haberse cargado igual, y reintentar se la cobra dos veces al
    cliente. Solo reintentamos cuando sabemos que no salió nada.
    """
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(e, requests.exceptions.ProxyError):
        return True   # no se pudo ni establecer el túnel con el proxy
    if isinstance(e, requests.exceptions.ReadTimeout):
        return False  # el server recibió; no sabemos qué hizo
    # ConnectionError "pelado" (DNS, connection refused) tampoco llegó a mandar
    # el body. ReadTimeout ya quedó descartado arriba.
    return isinstance(e, requests.exceptions.ConnectionError)


def _sin_ruta(e: Exception | None = None) -> GrowiUnavailable:
    """Mensaje de red uniforme, sin volcarle al vendedor el traceback con IPs y
    puertos internos (que es lo que se veía en el informe: 'ProxyError ...
    13.37.44.57:8888'). Se lanza recién cuando se agotaron TODOS los proxies."""
    if e is not None:
        print(f"[growi] sin ruta hacia el CRM (último error: {e!r})", flush=True)
    print(f"[growi] estado del pool: {_POOL.estado()}", flush=True)
    return GrowiUnavailable(
        "No se puede conectar con el CRM de Growi en este momento. "
        "Es un problema de conexión, no de tus datos: probá de nuevo en unos minutos."
    )


def _login_por(proxy: str | None) -> requests.Session:
    """
    Inicia sesión contra el CRM saliendo por `proxy`. La sesión queda atada a la
    IP/user-agent desde la que se loguea, por eso no sirve copiar cookies del
    navegador ni reusar una sesión entre proxies distintos.

    Deja subir el error de red tal cual: quien llama decide si prueba el
    siguiente proxy o se rinde.
    """
    session = requests.Session()
    session.headers.update({"user-agent": _USER_AGENT})
    session.proxies.update(proxies_de(proxy))

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

    if check.status_code != 200:
        # Credenciales mal: NO es culpa del proxy. Se propaga para que el
        # failover no vaya a probar los otros con el mismo usuario y password
        # equivocados, marcándolos muertos de paso.
        raise NotImplementedError(
            "Login a Growi falló. Revisar GROWI_CRM_EMAIL / GROWI_CRM_PASSWORD."
        )

    return session


def _login_con_failover() -> tuple[requests.Session, str | None]:
    """Se loguea por el primer proxy que responda. Devuelve (sesión, proxy).

    Éste es el corazón del failover: si el proxy principal está caído, en vez de
    quedar sin servicio hasta que alguien redeploye, pasamos al siguiente de la
    lista y lo dejamos marcado para no volver a pagarle el timeout.
    """
    ultimo_error = None
    for proxy in _POOL.candidatos():
        try:
            session = _login_por(proxy)
        except Exception as e:
            if not _es_error_de_red(e):
                raise           # credenciales, TLS, etc: cambiar de proxy no ayuda
            ultimo_error = e
            _POOL.marcar_muerto(proxy)
            print(f"[growi] proxy caído, pruebo el siguiente: "
                  f"{_ofuscar(proxy) if proxy else 'directo'} ({e.__class__.__name__})",
                  flush=True)
            continue
        _POOL.marcar_vivo(proxy)
        if proxy:
            print(f"[growi] sesión abierta via {_ofuscar(proxy)}", flush=True)
        return session, proxy

    raise _sin_ruta(ultimo_error)


def _get_session() -> requests.Session:
    global _session, _session_proxy
    with _session_lock:
        if _session is None:
            if not EMAIL or not PASSWORD:
                raise NotImplementedError(
                    "Growi no configurado. Agregar GROWI_CRM_EMAIL y GROWI_CRM_PASSWORD al .env"
                )
            _session, _session_proxy = _login_con_failover()
        return _session


def _descartar_sesion(por_proxy_caido: bool = False) -> None:
    """Tira la sesión cacheada. Con por_proxy_caido marcamos además el proxy para
    que el próximo login arranque directamente por otro."""
    global _session, _session_proxy
    with _session_lock:
        if por_proxy_caido:
            _POOL.marcar_muerto(_session_proxy)
        _session, _session_proxy = None, None


# Preflight: resultado cacheado para no pagar un round-trip al CRM en cada
# generación. El OK vale un rato largo (si anda, va a seguir andando); la falla
# vence rápido para que apenas vuelva el proxy se pueda trabajar de nuevo.
_PREFLIGHT_OK_TTL = 120.0
_PREFLIGHT_FAIL_TTL = 15.0
_preflight_cache: tuple[float, Exception | None] | None = None


def _preflight_reset() -> None:
    """Invalida el cache del preflight. Lo usa el monitor: si midiera sobre el
    cache estaría reportando el resultado de un request viejo de un vendedor en
    vez del estado actual."""
    global _preflight_cache
    _preflight_cache = None


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

    # Recorre el pool igual que el login: alcanza con que UNO responda. De paso
    # actualiza las marcas de vivo/muerto, así el envío posterior ya arranca por
    # el proxy correcto en vez de volver a descubrir cuál está caído.
    ultimo_error = None
    for proxy in _POOL.candidatos():
        try:
            requests.get(f"{CRM_URL}/cuenta/login.php", proxies=proxies_de(proxy),
                         headers={"user-agent": _USER_AGENT},
                         allow_redirects=False, timeout=_TIMEOUT)
        except Exception as e:
            if _es_error_de_red(e):
                ultimo_error = e
                _POOL.marcar_muerto(proxy)
                continue
            # Cualquier otra cosa (un 500 del CRM, TLS raro) no es "no hay ruta":
            # dejamos seguir y que falle donde corresponde, con su propio mensaje.
            print(f"[growi] preflight no concluyente: {e!r}", flush=True)
            return
        _POOL.marcar_vivo(proxy)
        _preflight_cache = (ahora, None)
        return

    fallo = _sin_ruta(ultimo_error)
    _preflight_cache = (ahora, fallo)
    raise fallo


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
            if not _es_error_de_red(e):
                raise
            if not _falló_al_conectar(e):
                # El POST salió y se cortó esperando la respuesta: la orden pudo
                # haber entrado. Reintentar acá duplica la carga, así que
                # cortamos y que un humano revise el CRM antes de reenviar.
                print(f"[growi] timeout de lectura en el envío: la orden PUEDE "
                      f"haber entrado, no reintento ({e!r})", flush=True)
                raise GrowiUnavailable(
                    "Se cortó la conexión esperando la respuesta del CRM. "
                    "Revisá en Growi si la orden entró antes de volver a mandarla.",
                    reintentable=False,
                ) from e
            # El proxy se murió con la campaña ya en curso. Antes esto era el
            # final del camino; ahora lo marcamos y reintentamos por otro. Es el
            # caso más caro de perder, porque los comentarios ya están generados.
            print(f"[growi] se cayó el proxy durante el envío, reintento por otro "
                  f"({e.__class__.__name__})", flush=True)
            _descartar_sesion(por_proxy_caido=True)
            if intento == max_intentos:
                raise _sin_ruta(e) from e
            try:
                session = _get_session()
            except GrowiUnavailable:
                raise           # se agotaron todos los proxies, ya viene con su mensaje
            continue
        if resp.status_code != 401:
            break
        print(f"[growi] 401 en intento {intento}/{max_intentos}, reintentando con login fresco", flush=True)
        # 401 = la sesión no vale, pero el proxy anda: NO lo marcamos muerto.
        _descartar_sesion()
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
