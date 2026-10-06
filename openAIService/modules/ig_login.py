"""
Login a Instagram desde el server, para renovar la sesión del scraper sin la Mac.

Hasta esto, cargar una sesión nueva pedía sacar el `sessionid` del inspector de
un navegador de escritorio: desde el celular no hay forma de verlo. Si la cuenta
caía un sábado, alguien tenía que llegar a una compu. Acá el panel manda usuario
y contraseña, el server hace el mismo login que la web de Instagram y se queda
con las cookies que devuelve. La contraseña se usa para ese POST y se tira: no
se guarda en ningún lado ni se loguea.

Sale por el mismo proxy que el scrapeo (_ig_get/_ig_post). No es un detalle: una
sesión que nace en una IP y scrapea desde otra es justo lo que Instagram manda a
checkpoint (ver el comentario de IG_HTTP_PROXY en post_processor).

Instagram puede contestar cuatro cosas, y cada una se resuelve distinto:
  - logueado: listo, cookies.
  - pide código (2FA): se guarda el login a medio hacer y el panel pide el código.
  - pide "¿fuiste vos?" (checkpoint): se aprueba en la app de Instagram desde el
    celular y se vuelve a intentar. No se puede resolver desde acá.
  - error (contraseña, rate limit): se muestra tal cual.

El login a medio hacer vive en memoria: el servicio es un solo proceso, y si un
deploy lo borra entre la contraseña y el código, se vuelve a empezar. No vale
guardarlo en la base por eso.
"""
import secrets
import threading
import time

import requests as req

from modules.post_processor import _ig_req

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
_BASE = "https://www.instagram.com"
_APP_ID = "936619743392459"

# Cuánto espera un login con 2FA a que llegue el código. Un SMS tarda menos de
# un minuto; más que esto es alguien que se fue y dejó cookies a medio armar.
_TTL_PENDIENTE = 10 * 60

# Cuánto se recuerda el "dispositivo" (el tarro de cookies) de un login que
# terminó en «Fui yo», para que el reintento salga del mismo.
_TTL_DISPOSITIVO = 30 * 60

_pendientes: dict = {}
# username → {sesion, vence}. Instagram aprueba el «Fui yo» para el dispositivo
# que intentó entrar, y el dispositivo son las cookies (mid, ig_did, datr). Si
# cada «Entrar» arranca un tarro vacío, cada reintento es un dispositivo nuevo
# que pide su propio «Fui yo»: un loop sin salida (pasó el 2026-10-05 desde el
# iPhone, una docena de intentos). Por eso el reintento reusa el tarro.
_dispositivos: dict = {}
_lock = threading.Lock()


def _headers(sesion: req.Session, referer: str) -> dict:
    return {
        "User-Agent": _UA,
        "x-ig-app-id": _APP_ID,
        "x-asbd-id": "359341",
        "x-requested-with": "XMLHttpRequest",
        "x-csrftoken": _cookies_de(sesion).get("csrftoken", ""),
        "Origin": _BASE,
        "Referer": referer,
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    }


def _cookies_de(sesion: req.Session) -> dict:
    # Se recorre el tarro en vez de usar cookies.get(): si Instagram manda la
    # misma cookie para dos dominios, .get() tira CookieConflictError.
    out = {}
    for c in sesion.cookies:
        if c.domain.endswith("instagram.com") and c.value:
            out[c.name] = c.value
    return out


def _json(r) -> dict:
    # Los rechazos (2FA, checkpoint, contraseña) vienen con status 400 pero con
    # JSON adentro: se mira el cuerpo, no el status.
    try:
        d = r.json()
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _limpiar_vencidos() -> None:
    ahora = time.time()
    with _lock:
        for tabla in (_pendientes, _dispositivos):
            for k in [k for k, v in tabla.items() if v["vence"] < ahora]:
                tabla.pop(k, None)


def _preparar(sesion: req.Session) -> None:
    """Visita la página de login para que Instagram setee csrftoken y mid, como
    haría un navegador. Sin csrftoken el POST del login da 403."""
    _ig_req(sesion.get, f"{_BASE}/accounts/login/", headers={"User-Agent": _UA},
            timeout=15)
    if not _cookies_de(sesion).get("csrftoken"):
        # A veces la página no lo manda como cookie: está en shared_data.
        r = _ig_req(sesion.get, f"{_BASE}/api/v1/web/data/shared_data/",
                    headers=_headers(sesion, f"{_BASE}/accounts/login/"),
                    timeout=15)
        token = ((_json(r).get("config") or {}).get("csrf_token") or "")
        if token:
            sesion.cookies.set("csrftoken", token, domain=".instagram.com")


def _interpretar(d: dict, r, sesion: req.Session, username: str) -> dict:
    """La respuesta de Instagram → {paso, ...} para el panel."""
    cookies = _cookies_de(sesion)
    if d.get("authenticated") and cookies.get("sessionid"):
        with _lock:
            _dispositivos.pop(username, None)
        print(f"[ig_login] @{username}: ok", flush=True)
        return {"paso": "ok", "cookies": cookies}

    if d.get("two_factor_required"):
        info = d.get("two_factor_info") or {}
        metodo = "app" if info.get("totp_two_factor_on") else "sms"
        login_id = secrets.token_urlsafe(16)
        with _lock:
            _pendientes[login_id] = {
                "sesion": sesion,
                "username": info.get("username") or username,
                "identifier": info.get("two_factor_identifier", ""),
                "metodo": metodo,
                "vence": time.time() + _TTL_PENDIENTE,
            }
        destino = info.get("obfuscated_phone_number") or ""
        print(f"[ig_login] @{username}: pide código ({metodo})", flush=True)
        return {"paso": "codigo", "login_id": login_id, "metodo": metodo,
                "destino": destino}

    if d.get("message") == "checkpoint_required" or d.get("checkpoint_url"):
        with _lock:
            _dispositivos[username] = {"sesion": sesion,
                                       "vence": time.time() + _TTL_DISPOSITIVO}
        print(f"[ig_login] @{username}: checkpoint (pide «Fui yo»)", flush=True)
        return {"paso": "checkpoint",
                "detalle": "Instagram quiere confirmar que fuiste vos. Abrí la app "
                           "de Instagram con esa cuenta, tocá «Fui yo» y volvé a "
                           "tocar Entrar."}

    if d.get("user") is False:
        return {"paso": "error", "detalle": f"Instagram no encuentra la cuenta @{username}."}
    if d.get("user") and d.get("authenticated") is False:
        return {"paso": "error", "detalle": "La contraseña no es correcta."}

    mensaje = d.get("message") or ""
    if r.status_code == 429 or "wait a few minutes" in mensaje.lower():
        return {"paso": "error",
                "detalle": "Instagram está frenando los intentos de login desde el "
                           "server. Esperá unos minutos antes de reintentar."}
    print(f"[ig_login] respuesta no reconocida: status {r.status_code}, "
          f"claves {sorted(d.keys())}", flush=True)
    return {"paso": "error",
            "detalle": mensaje or f"Instagram respondió {r.status_code} sin explicar por qué."}


def iniciar(username: str, password: str) -> dict:
    """Primer paso: usuario y contraseña. Devuelve {paso: ok|codigo|checkpoint|error}."""
    username = (username or "").strip().lstrip("@").lower()
    if not username or not password:
        return {"paso": "error", "detalle": "Faltan el usuario o la contraseña."}
    _limpiar_vencidos()
    with _lock:
        previo = _dispositivos.get(username)
    sesion = previo["sesion"] if previo else req.Session()
    try:
        _preparar(sesion)
        r = _ig_req(
            sesion.post,
            f"{_BASE}/api/v1/web/accounts/login/ajax/",
            data={
                # Versión 0 = contraseña en claro dentro del sobre. Es lo que
                # manda la web cuando no cifra del lado del navegador, y viaja
                # por HTTPS igual.
                "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}",
                "username": username,
                "queryParams": "{}",
                "optIntoOneTap": "false",
                "trustedDeviceRecords": "{}",
            },
            headers=_headers(sesion, f"{_BASE}/accounts/login/"),
            timeout=20,
            allow_redirects=False,
        )
    except req.RequestException as e:
        return {"paso": "error", "detalle": f"No pude hablar con Instagram: {type(e).__name__}"}
    return _interpretar(_json(r), r, sesion, username)


def confirmar_codigo(login_id: str, codigo: str) -> dict:
    """Segundo paso, solo si Instagram pidió 2FA."""
    _limpiar_vencidos()
    with _lock:
        p = _pendientes.get(login_id or "")
    if not p:
        return {"paso": "error",
                "detalle": "Ese login ya venció (o el servicio se reinició). "
                           "Volvé a poner la contraseña."}
    codigo = "".join(ch for ch in (codigo or "") if ch.isdigit())
    if not codigo:
        return {"paso": "error", "detalle": "Falta el código."}
    sesion = p["sesion"]
    try:
        r = _ig_req(
            sesion.post,
            f"{_BASE}/api/v1/web/accounts/login/ajax/two_factor/",
            data={
                "identifier": p["identifier"],
                "username": p["username"],
                "verificationCode": codigo,
                "verification_method": "3" if p["metodo"] == "app" else "1",
                "trust_signal": "true",
                "queryParams": "{}",
            },
            headers=_headers(sesion, f"{_BASE}/accounts/login/two_factor/"),
            timeout=20,
            allow_redirects=False,
        )
    except req.RequestException as e:
        return {"paso": "error", "detalle": f"No pude hablar con Instagram: {type(e).__name__}"}
    res = _interpretar(_json(r), r, sesion, p["username"])
    if res["paso"] == "error":
        # Lo más común es un código mal tipeado o vencido: el login sigue
        # pendiente, así se reintenta el código sin volver a la contraseña.
        res["login_id"] = login_id
        res["detalle"] = ("Instagram no aceptó el código. Revisalo (o esperá el "
                          "siguiente) y probá de nuevo.")
        return res
    with _lock:
        _pendientes.pop(login_id, None)
    return res
