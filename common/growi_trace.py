"""
Trazabilidad de los envíos de órdenes al CRM de Growi.

Cada envío queda registrado en la tabla `growi_calls` con su payload, su
respuesta cruda, el status, la duración y el proxy por el que salió.

Solo el envío: el resto del tráfico al CRM (login, preflight, hora del server,
listado de ventas) son consultas de apoyo que se repiten todo el tiempo, no
mueven plata, y guardarlas tapaba lo único que hace falta reconstruir.

Existe por un problema concreto: el CRM es de un tercero y no tiene panel que
nos sirva. Cuando un vendedor decía "mandé la orden y no entró", lo único
disponible era el print del proceso vivo — y los logs se rotan, así que al día
siguiente ya no había nada que mirar. Ahora se puede contestar exactamente qué
se mandó y qué contestaron.

Dos reglas de oro:
  1. Trazar NUNCA puede romper el flujo. Cualquier error de acá se traga y se
     loguea: es preferible perder la auditoría que perder una campaña.
  2. Nada de secretos en la tabla. Las passwords (la del CRM y la del proxy) se
     redactan antes de guardar.

Uso típico:

    with trazar("enviar_trafico", "POST", url, payload=payload) as tr:
        resp = session.post(url, json=payload)
        tr.respuesta(resp)
"""
import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

# Cuánto guardamos de cada cuerpo. La respuesta del CRM cuando la sesión se cae
# es una página de login entera: sin tope, cada fila serían ~50 KB de HTML
# inútil. Con 20k alcanza para ver el JSON completo de un envío grande.
MAX_BODY = int(os.environ.get("GROWI_TRACE_MAX_BODY", "20000"))

# Claves cuyo valor nunca se guarda, sin importar dónde aparezcan.
_SECRETAS = ("password", "passwd", "pass", "token", "authorization", "cookie",
             "secret", "apikey", "api_key")

# Contexto de la operación en curso (quién la disparó y sobre qué). Es un
# ContextVar y no un parámetro porque las capas de abajo (el cliente HTTP) no
# conocen la sesión web, y pasarlo a mano por cada firma ensuciaría todo.
_ctx: ContextVar[dict] = ContextVar("growi_trace_ctx", default={})

# ¿Ya se guardó una traza en esta operación? Lo usa `rechazo_local` para no
# duplicar la fila cuando el request SÍ salió y falló después.
_trazado: ContextVar[bool] = ContextVar("growi_trace_trazado", default=False)


def contexto_actual() -> dict:
    return dict(_ctx.get() or {})


@contextmanager
def contexto(**campos):
    """Marca de quién es lo que se va a hacer contra el CRM.

    Campos usados: origen, account_id, user_id, username, post_url,
    client_ig_username, idventa, idvendedor, costo, trace_id.
    """
    base = contexto_actual()
    base.update({k: v for k, v in campos.items() if v is not None})
    base.setdefault("trace_id", uuid.uuid4().hex[:32])
    token = _ctx.set(base)
    try:
        yield base
    finally:
        _ctx.reset(token)


def _redactar(valor, _prof=0):
    """Copia el dato sin secretos. Corta en profundidad 8 por si viniera algo
    ciclado o absurdamente anidado."""
    if _prof > 8:
        return "…"
    if isinstance(valor, dict):
        out = {}
        for k, v in valor.items():
            if any(s in str(k).lower() for s in _SECRETAS):
                out[k] = "***"
            else:
                out[k] = _redactar(v, _prof + 1)
        return out
    if isinstance(valor, (list, tuple)):
        return [_redactar(v, _prof + 1) for v in valor]
    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor
    return str(valor)[:500]


def _acotar_payload(payload):
    """Redacta y, si el JSON quedó enorme, lo reemplaza por un resumen.

    Un envío de comentarios puede traer 200 textos: guardar eso entero por fila
    infla la tabla sin agregar nada que no esté en la orden misma.
    """
    if payload is None:
        return None
    limpio = _redactar(payload)
    try:
        crudo = json.dumps(limpio, ensure_ascii=False)
    except Exception:
        return {"_no_serializable": str(payload)[:MAX_BODY]}
    if len(crudo) <= MAX_BODY * 4:
        return limpio
    return {"_truncado": True, "_bytes": len(crudo),
            "_muestra": crudo[:MAX_BODY]}


def _headers(h) -> dict | None:
    """Headers a dict plano, redactados. `cookie` y `set-cookie` son sesión del
    CRM: guardarlos sería dejar una credencial usable en la tabla."""
    if not h:
        return None
    try:
        return _redactar({str(k).lower(): str(v) for k, v in h.items()})
    except Exception:
        return None


def _texto(resp) -> str:
    try:
        return (resp.text or "")[:MAX_BODY]
    except Exception:
        return ""


def _ofuscar_proxy(proxy) -> str:
    if not proxy:
        return ""
    try:
        from common.proxy_pool import _ofuscar
        return _ofuscar(str(proxy))[:200]
    except Exception:
        return str(proxy)[:200]


class Traza:
    """Una llamada al CRM en curso. La crea `trazar`; el llamador solo le pasa
    la respuesta (o deja subir la excepción, que se registra sola)."""

    def __init__(self, operacion, method, url, payload=None, **extra):
        self.operacion = operacion
        self.method = (method or "GET").upper()
        self.url = url
        self.payload = payload
        self.extra = {k: v for k, v in extra.items() if v is not None}
        self.status_code = None
        self.body = None
        # Los headers de ida y de vuelta. Sin ellos la traza cuenta la mitad de
        # la historia: el CRM se comporta distinto según el referer y el
        # x-requested-with, y su respuesta trae el content-type que delata
        # cuándo nos devolvió HTML de login en lugar del JSON esperado.
        self.request_headers = None
        self.response_headers = None
        self.ok = False
        self.error = None
        self.intentos = 1
        self.proxy = None
        self._t0 = time.monotonic()

    # — lo que llena el llamador —

    def respuesta(self, resp, ok=None):
        """Registra la respuesta HTTP. Por defecto, ok = status < 400 y, si el
        cuerpo es el JSON del CRM, que además traiga success=true: un 200 con
        success=false es un rechazo y tiene que verse como tal en el panel."""
        self.status_code = getattr(resp, "status_code", None)
        self.body = _texto(resp)
        self.response_headers = _headers(getattr(resp, "headers", None))
        # requests deja colgado el request REAL que salió (con los headers de
        # sesión ya mezclados). Es lo más fiel que podemos guardar de la ida.
        enviado = getattr(resp, "request", None)
        if enviado is not None:
            self.request_headers = _headers(getattr(enviado, "headers", None))
            if self.payload is None and getattr(enviado, "body", None):
                cuerpo = enviado.body
                if isinstance(cuerpo, bytes):
                    cuerpo = cuerpo.decode("utf-8", "replace")
                self.payload = {"_cuerpo": cuerpo[:MAX_BODY]}
        if ok is not None:
            self.ok = bool(ok)
            return
        self.ok = bool(self.status_code and self.status_code < 400)
        if self.ok and self.body.lstrip().startswith("{"):
            try:
                data = json.loads(self.body)
            except Exception:
                return
            if isinstance(data, dict) and "success" in data:
                self.ok = bool(data.get("success"))

    def fallo(self, e):
        self.ok = False
        self.error = f"{e.__class__.__name__}: {e}"[:2000]

    def intento(self, n):
        """Cuántos intentos gastó esta llamada (relogin por sesión caída, 401)."""
        self.intentos = max(self.intentos, int(n or 1))

    def headers(self, request_headers=None):
        """Headers de la ida, para el caso en que la request nunca llegó a
        salir (error de red): ahí no hay `resp.request` de dónde sacarlos."""
        if request_headers and not self.request_headers:
            self.request_headers = _headers(request_headers)

    def via(self, proxy):
        self.proxy = _ofuscar_proxy(proxy)

    def datos(self, **campos):
        """Agrega contexto descubierto en el camino (idventa resuelto, etc.)."""
        self.extra.update({k: v for k, v in campos.items() if v is not None})

    # — persistencia —

    def _fila(self) -> dict:
        ctx = contexto_actual()
        ctx.update(self.extra)
        return {
            "trace_id": ctx.get("trace_id"),
            "origen": ctx.get("origen") or "web",
            "operacion": self.operacion,
            "method": self.method,
            "url": self.url,
            "account_id": ctx.get("account_id"),
            "user_id": ctx.get("user_id"),
            "username": ctx.get("username"),
            "request_payload": _acotar_payload(self.payload),
            "request_headers": _redactar(self.request_headers) if self.request_headers else None,
            "response_body": self.body,
            "response_headers": self.response_headers,
            "status_code": self.status_code,
            "ok": self.ok,
            "duracion_ms": int((time.monotonic() - self._t0) * 1000),
            "intentos": self.intentos,
            "proxy": self.proxy or _ofuscar_proxy(ctx.get("proxy")),
            "error": self.error,
            "post_url": ctx.get("post_url"),
            "client_ig_username": ctx.get("client_ig_username"),
            "idventa": str(ctx.get("idventa")) if ctx.get("idventa") is not None else None,
            "idvendedor": str(ctx.get("idvendedor")) if ctx.get("idvendedor") is not None else None,
            "costo": ctx.get("costo"),
        }

    def guardar(self) -> None:
        fila = None
        _trazado.set(True)
        try:
            fila = self._fila()
            from common import repository
            repository.registrar_llamada_crm(**fila)
        except Exception as e:
            # La auditoría no puede tumbar el envío. Queda el print, que es
            # exactamente lo que había antes de esta tabla.
            print(f"[growi-trace] no pude guardar la traza: {e!r}", flush=True)
        if fila is not None:
            print(f"[growi-trace] {fila['operacion']} {fila['method']} "
                  f"{fila['status_code'] or fila['error'] or '—'} "
                  f"({fila['duracion_ms']}ms, {fila['intentos']} intento/s)", flush=True)


def registrar_fallo(operacion, error, url="", method="POST", payload=None, **extra):
    """Deja una fila de auditoría por un envío que NUNCA llegó a salir.

    `trazar` solo registra lo que se le pide al CRM; un envío que se frena antes
    (no sabemos la campaña, la cuenta no tiene CRM configurado, el saldo no da)
    no generaba ninguna fila, y el panel de trazas mostraba exactamente nada
    justo en el caso en que el vendedor jura que mandó la orden. Ahora todo
    fallo queda registrado, haya habido request o no.

    Como el resto de la auditoría: no puede romper el flujo (ver `guardar`).
    """
    tr = Traza(operacion, method, url, payload=payload, **extra)
    tr.fallo(error)
    tr.guardar()
    return tr


@contextmanager
def rechazo_local(operacion, url, **extra):
    """Deja fila cuando la orden se cae ANTES de que el request salga.

    Existe por un agujero concreto: `trazar` solo envuelve el round-trip HTTP,
    así que un envío frenado acá (la cuenta sin campaña activa, sin idvendedor,
    sin CRM propio, el proxy que ni conecta) no quedaba en ningún lado. No está
    en el CRM porque nunca llegó, y tampoco estaba en la auditoría: el vendedor
    veía el error una vez en pantalla y después no había forma de saber cuántas
    órdenes se habían rebotado ni por qué.

    Si el request sí salió, la fila ya la escribió `trazar` y acá no se hace
    nada: la operación se marca como trazada en `_trazado`.
    """
    token = _trazado.set(False)
    try:
        yield
    except Exception as e:
        if not _trazado.get():
            # El trace_id se fija ACÁ y se mete en `extra` (de donde `_fila` lo
            # toma) en vez de dejar que lo resuelva la fila: hace falta conocerlo
            # para colgárselo a la excepción, y si no hay un `contexto()` activo
            # la fila lo guardaría en None y no habría con qué correlacionar.
            tid = (contexto_actual().get("trace_id") or extra.get("trace_id")
                   or uuid.uuid4().hex[:32])
            extra["trace_id"] = tid
            registrar_fallo(operacion, e, url=url, **extra)
            # Se le cuelga a la excepción QUÉ fila de auditoría la representa. Lo
            # usa la pantalla de "órdenes que no entraron": cuando además
            # guardamos la orden para reintentarla, hay que mostrar UNA sola cosa
            # (la que tiene botón) y no la orden y su fila por separado, que se
            # ve como si hubiera fallado dos veces.
            try:
                e.growi_trace_id = tid
            except Exception:
                pass    # excepciones raras que no aceptan atributos
        raise
    finally:
        _trazado.reset(token)


@contextmanager
def trazar(operacion, method, url, payload=None, **extra):
    """Envuelve una llamada al CRM. Registra siempre: salga bien, la rechacen o
    reviente la red. Las excepciones se re-lanzan tal cual."""
    tr = Traza(operacion, method, url, payload=payload, **extra)
    try:
        yield tr
    except Exception as e:
        tr.fallo(e)
        tr.guardar()
        raise
    tr.guardar()
