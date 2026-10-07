"""
Login a Instagram con un navegador de verdad (Chromium headless) en el server.

El login por HTTP (ig_login) se queda corto apenas Instagram pide verificar
que fuiste vos: desde 2026 eso es /auth_platform/codeentry/, una pantalla de
Bloks con tokens opacos que no hay forma prolija de imitar. Un navegador no
imita nada: Instagram le muestra la misma pantalla que a una persona, y acá
solo se escribe en ella lo que el panel manda.

Cómo vive un login:
  - cada login es un thread dueño de su Chromium (Playwright sync no deja usar
    el navegador desde otro thread, y Flask atiende cada request en uno
    distinto). El thread recibe órdenes por una cola y contesta por otra;
  - iniciar() entra con usuario y contraseña y devuelve ok, codigo o error;
  - si quedó en codigo, el thread sigue con la página abierta esperando
    confirmar_codigo(). Si Instagram solo espera que apruebes en la app, el
    código puede venir vacío: se mira si la página ya entró;
  - al terminar (ok, error o _TTL sin noticias) se cierra el navegador.

Sale por el mismo proxy que el scrapeo, por la misma razón que ig_login: una
sesión que nace en una IP y scrapea desde otra va derecho a checkpoint.

La contraseña se escribe en el formulario y se suelta. No se loguea nunca; los
logs llevan la ruta de la página (sin query) y un pedazo del texto visible,
para poder ajustar contra lo que Instagram muestre de verdad.
"""
import queue
import re
import secrets
import threading
import time
from urllib.parse import unquote, urlsplit

_BASE = "https://www.instagram.com"
_DOMINIO = "instagram.com"
# El mismo que el scraper (post_processor): la sesión nace y se usa con la
# misma cara.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

# Cuánto espera la página abierta a que llegue el código. Más que esto es
# alguien que se fue, y un Chromium abierto son ~200 MB.
_TTL = 10 * 60
# Cuánto se espera a que Instagram reaccione después de mandar algo. Tiene que
# entrar con aire en los 90 s que el webService le da a la llamada.
_ESPERA = 35
# Dos logins a la vez como mucho: cada uno es un navegador entero.
_CUPO = threading.BoundedSemaphore(2)

_pendientes: dict = {}
_lock = threading.Lock()

_SEL_USUARIO = ('input[name="username"], input[name="email"], '
                'input[autocomplete="username"]')
_SEL_PASSWORD = 'input[name="password"], input[name="pass"], input[type="password"]'
# En las pantallas de verificación el único campo de texto es el del código.
# La de auth_platform no le pone ningún atributo distintivo (visto en prod el
# 2026-10-06), así que vale cualquier input de texto visible.
_SEL_CODIGO = ('input[name="verificationCode"], input[name="security_code"], '
               'input[autocomplete="one-time-code"], input[inputmode="numeric"], '
               'input[type="tel"], input[type="number"], input[type="text"], '
               'input:not([type])')
_RE_COOKIES = re.compile(r"(permitir todas las cookies|allow all cookies|"
                         r"rechazar cookies opcionales|decline optional cookies)", re.I)
_RE_ENVIAR = re.compile(r"^(enviar código|send security code|send code|enviar|send|"
                        r"continuar|continue|siguiente|next|confirmar|confirm|submit)$", re.I)
_RE_PASS_MALA = re.compile(r"(contraseña.{0,40}(incorrecta|no es correcta)|"
                           r"password.{0,40}incorrect|"
                           r"no pertenece a ninguna cuenta|doesn.t belong to an account)", re.I)
_RE_FRENO = re.compile(r"(espera unos minutos|wait a few minutes|try again later|"
                       r"vuelve a intentarlo más tarde)", re.I)
_RE_AHORA_NO = re.compile(r"^(ahora no|not now)$", re.I)
_RE_DESTINO = re.compile(r"[\w.*]*\*{2,}[\w.*]*@[\w.*-]+|\+?\d[\d* ]{3,}\*{2,}[\d* ]*\d")


def disponible() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _proxy() -> dict | None:
    """El primer proxy vivo del pool del scraper, en el formato de Playwright
    (que no acepta user:pass dentro de la URL)."""
    from modules.post_processor import _IG_POOL
    raw = _IG_POOL.candidatos()[0]
    if not raw:
        return None
    u = urlsplit(raw if "://" in raw else "http://" + raw)
    out = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        out["username"] = unquote(u.username)
        out["password"] = unquote(u.password or "")
    return out


def _ruta(url: str) -> str:
    ruta = urlsplit(url).path
    return "/".join(ruta.split("/")[:3]) + "/"


def _texto(page, n: int = 220) -> str:
    try:
        t = page.inner_text("body", timeout=2000)
    except Exception:
        return ""
    return " ".join(t.split())[:n]


class _Login(threading.Thread):
    def __init__(self, username: str, password: str):
        super().__init__(daemon=True, name=f"ig_login_nav:{username}")
        self.username = username
        self._password = password
        self.ordenes: queue.Queue = queue.Queue()
        self.respuestas: queue.Queue = queue.Queue()

    # ── helpers de página ────────────────────────────────────────────────────
    def _log(self, que: str, page=None) -> None:
        extra = ""
        if page is not None:
            extra = f" en {_ruta(page.url)} «{_texto(page)}»"
        print(f"[ig_login_nav] @{self.username}: {que}{extra}", flush=True)

    def _cookies(self, ctx) -> dict:
        return {c["name"]: c["value"] for c in ctx.cookies()
                if c["domain"].endswith(_DOMINIO) and c["value"]}

    def _aceptar_cookies(self, page) -> None:
        try:
            boton = page.get_by_role("button", name=_RE_COOKIES).first
            if boton.is_visible(timeout=1500):
                boton.click()
        except Exception:
            pass

    def _visible(self, page, selector: str):
        loc = page.locator(selector)
        for i in range(min(loc.count(), 6)):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                pass
        return None

    def _campos(self, page) -> list:
        """Atributos de los inputs visibles, para el log (nunca el valor)."""
        try:
            return page.eval_on_selector_all("input", """els => els
                .filter(e => e.offsetParent !== null)
                .map(e => ['type','name','autocomplete','inputmode','aria-label']
                    .map(a => e.getAttribute(a)).filter(Boolean).join('/'))""")
        except Exception:
            return []

    def _estado(self, page, ctx) -> dict | None:
        """Mira la página una vez. None = todavía no se sabe, seguir esperando."""
        cookies = self._cookies(ctx)
        if cookies.get("sessionid"):
            return {"paso": "ok", "cookies": cookies}
        texto = _texto(page, 600)
        if _RE_PASS_MALA.search(texto):
            return {"paso": "error", "detalle": "Instagram dice que el usuario o la "
                                                "contraseña no son correctos."}
        if _RE_FRENO.search(texto):
            return {"paso": "error", "detalle": "Instagram está frenando los intentos "
                                                "de login. Esperá unos minutos."}
        url = page.url
        en_verificacion = any(s in url for s in ("/challenge", "/auth_platform",
                                                 "/two_factor", "/checkpoint"))
        if not en_verificacion:
            return None
        if "/recaptcha" in url:
            # Un captcha es Instagram diciendo «esto parece un bot». No se
            # resuelve desde acá.
            return {"paso": "error", "detalle": (
                f"Instagram pidió un captcha para @{self.username}: está desconfiando "
                "del server. Entrá con esa cuenta en la app y, si te pide verificar "
                "algo, hacelo; después esperá unas horas antes de reintentar acá.")}
        if len(texto) < 20:
            # La pantalla se arma con JS: vacía todavía no dice nada.
            return None
        destino = (_RE_DESTINO.search(texto) or [""])[0] if texto else ""
        if self._visible(page, _SEL_CODIGO):
            metodo = "app" if "autenticación" in texto.lower() or "authentication app" in texto.lower() else "codigo"
            return {"paso": "codigo", "metodo": metodo, "destino": destino}
        # Elegir a dónde mandar el código (challenge viejo): se acepta lo que
        # Instagram propone y se vuelve a mirar.
        try:
            boton = page.get_by_role("button", name=_RE_ENVIAR).first
            if boton.is_visible(timeout=500):
                self._log("toca el botón de enviar código", page)
                boton.click()
                return None
        except Exception:
            pass
        # Sin campo ni botón: es la pantalla de «revisá la notificación en tu
        # otro dispositivo». Se aprueba en la app y la página entra sola.
        return {"paso": "codigo", "metodo": "aprobar", "destino": destino}

    def _esperar(self, page, ctx, salir_si_codigo: bool = True, espera: float = _ESPERA) -> dict:
        """Mira la página hasta que haya un resultado. Con salir_si_codigo=False
        la pantalla de código no cuenta como resultado: se espera a que entre
        (o falle), que es lo que pasa después de mandar el código o aprobar."""
        fin = time.time() + espera
        ultimo = None
        while time.time() < fin:
            self._aceptar_cookies(page)
            ultimo = self._estado(page, ctx)
            if ultimo and (salir_si_codigo or ultimo["paso"] != "codigo"):
                return ultimo
            page.wait_for_timeout(1000)
        if ultimo:
            return ultimo
        self._log("no reconozco la página", page)
        return {"paso": "error", "detalle": "Instagram mostró algo que no sé leer. "
                                            "Volvé a intentar en un rato."}

    def _asentar(self, page, ctx) -> dict:
        """El sessionid aparece antes de que el login termine: la página todavía
        está saliendo de la verificación, y csrftoken y compañía cambian en los
        segundos siguientes. Tomar las cookies en ese instante dio una sesión
        que Instagram rechazaba con 400 (prod, 2026-10-07). Se espera a que la
        página salga, se entra al inicio como haría una persona, y recién ahí
        se toman."""
        fin = time.time() + 15
        while time.time() < fin and any(s in page.url for s in ("/challenge", "/auth_platform",
                                                                "/two_factor", "/checkpoint")):
            page.wait_for_timeout(1000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.goto(f"{_BASE}/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            for _ in range(2):
                boton = page.get_by_role("button", name=_RE_AHORA_NO).first
                if boton.is_visible():
                    boton.click()
                    page.wait_for_timeout(1500)
        except Exception as e:
            self._log(f"no pude terminar de entrar al inicio ({type(e).__name__})")
        if "/accounts/suspended" in page.url:
            # «Confirma que eres una persona real»: Instagram marcó la cuenta
            # como posible bot. Lo tiene que resolver una persona en la app; la
            # sesión no sirve hasta entonces (la API da checkpoint_required).
            self._log("cuenta frenada por Instagram (verificar que es una persona)", page)
            return {"paso": "error", "detalle": (
                f"El login anduvo, pero Instagram frenó a @{self.username}: pide "
                "confirmar que es una persona real. Entrá con esa cuenta en la app de "
                "Instagram, seguí los pasos que te muestra y después volvé a cargarla acá.")}
        cookies = self._cookies(ctx)
        self._log(f"sesión asentada, cookies {sorted(cookies)}", page)
        return {"paso": "ok", "cookies": cookies}

    # ── pasos ────────────────────────────────────────────────────────────────
    def _entrar(self, page, ctx) -> dict:
        page.goto(f"{_BASE}/accounts/login/", wait_until="domcontentloaded", timeout=30000)
        self._aceptar_cookies(page)
        usuario = page.locator(_SEL_USUARIO).first
        try:
            usuario.wait_for(state="visible", timeout=20000)
        except Exception:
            # Visto en prod después de un captcha: Instagram deja de mostrarle
            # el formulario a esta IP. Reintentar enseguida lo empeora.
            self._log("Instagram no mostró el formulario de login", page)
            return {"paso": "error", "detalle": (
                "Instagram no mostró el formulario de login: está desconfiando del "
                "server. No reintentes por unas horas, que insistir lo empeora.")}
        usuario.fill(self.username)
        clave = page.locator(_SEL_PASSWORD).first
        clave.fill(self._password)
        self._password = None
        clave.press("Enter")
        page.wait_for_timeout(2500)
        # El botón de Instagram es un div con role=button: si el Enter no mandó
        # el formulario, se toca a mano.
        try:
            if "/accounts/login" in page.url and clave.is_visible() and clave.input_value():
                page.get_by_role("button", name=re.compile(r"^(iniciar sesión|log in)$", re.I)).first.click(timeout=3000)
                page.wait_for_timeout(2000)
        except Exception:
            pass
        res = self._esperar(page, ctx)
        self._log(f"después de la contraseña → {res['paso']}"
                  + (f" ({res.get('metodo')}, campos {self._campos(page)})"
                     if res["paso"] == "codigo" else ""), page)
        return res

    def _mandar_codigo(self, page, ctx, codigo: str) -> dict:
        if codigo:
            campo = self._visible(page, _SEL_CODIGO)
            if not campo:
                self._log(f"no encuentro el campo del código; campos {self._campos(page)}", page)
                return {"paso": "error", "sigue": True,
                        "detalle": "No encontré dónde escribir el código en la página de Instagram."}
            if campo:
                campo.click()
                campo.fill(codigo)
                if campo.input_value() != codigo:
                    # Algunos inputs de React ignoran fill(): se tipea.
                    campo.fill("")
                    campo.press_sequentially(codigo, delay=60)
                campo.press("Enter")
                page.wait_for_timeout(1500)
                try:
                    boton = page.get_by_role("button", name=_RE_ENVIAR).first
                    if campo.is_visible() and boton.is_visible(timeout=500) and boton.is_enabled():
                        boton.click()
                except Exception:
                    pass
        res = self._esperar(page, ctx, salir_si_codigo=False, espera=20)
        if res["paso"] == "codigo":
            res = {"paso": "error", "sigue": True, "detalle": (
                "Instagram no aceptó el código. Revisalo y probá de nuevo." if codigo else
                "Todavía no entró. Tocá «Fui yo» en la app de Instagram (o poné el "
                "código si te llegó) y volvé a tocar Confirmar.")}
        self._log(f"después del código → {res['paso']}", page)
        return res

    def run(self) -> None:
        from playwright.sync_api import sync_playwright
        navegador = None
        try:
            with _CUPO, sync_playwright() as pw:
                navegador = pw.chromium.launch(headless=True, proxy=_proxy(),
                                               args=["--no-sandbox", "--disable-dev-shm-usage"])
                ctx = navegador.new_context(user_agent=_UA, locale="es-AR",
                                            viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                res = self._entrar(page, ctx)
                if res["paso"] == "ok":
                    res = self._asentar(page, ctx)
                seguir = res["paso"] == "codigo"
                self.respuestas.put(res)
                while seguir:
                    try:
                        codigo = self.ordenes.get(timeout=_TTL)
                    except queue.Empty:
                        self._log("venció esperando el código")
                        break
                    if codigo is None:
                        break
                    res = self._mandar_codigo(page, ctx, codigo)
                    if res["paso"] == "ok":
                        res = self._asentar(page, ctx)
                    # Un código rechazado no mata el login: se reintenta.
                    seguir = bool(res.get("sigue"))
                    self.respuestas.put(res)
                navegador.close()
        except Exception as e:
            self._password = None
            # Solo la primera línea: el call log de Playwright que viene abajo
            # puede traer el valor de un fill(), o sea la contraseña.
            print(f"[ig_login_nav] @{self.username}: falló el navegador: "
                  f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}", flush=True)
            self.respuestas.put({"paso": "error",
                                 "detalle": f"No pude entrar con el navegador: {type(e).__name__}"})


def _esperar_respuesta(login: _Login, segundos: float) -> dict:
    try:
        return login.respuestas.get(timeout=segundos)
    except queue.Empty:
        return {"paso": "error", "detalle": "Instagram tardó demasiado en contestar. "
                                            "Probá de nuevo."}


def iniciar(username: str, password: str) -> dict:
    username = (username or "").strip().lstrip("@").lower()
    if not username or not password:
        return {"paso": "error", "detalle": "Faltan el usuario o la contraseña."}
    # Un login anterior de la misma cuenta que quedó esperando código se cierra:
    # si no, son dos navegadores peleando por la misma verificación.
    with _lock:
        for k in [k for k, v in _pendientes.items() if v.username == username]:
            _pendientes.pop(k).ordenes.put(None)
    login = _Login(username, password)
    login.start()
    res = _esperar_respuesta(login, 80)
    if res["paso"] == "codigo":
        login_id = secrets.token_urlsafe(16)
        with _lock:
            _pendientes[login_id] = login
        res["login_id"] = login_id
    elif res["paso"] == "error":
        login.ordenes.put(None)
    return res


def confirmar_codigo(login_id: str, codigo: str) -> dict:
    with _lock:
        login = _pendientes.get(login_id or "")
    if not login or not login.is_alive():
        with _lock:
            _pendientes.pop(login_id or "", None)
        return {"paso": "error",
                "detalle": "Ese login ya venció (o el servicio se reinició). "
                           "Volvé a poner la contraseña."}
    login.ordenes.put("".join(ch for ch in (codigo or "") if ch.isdigit()))
    res = _esperar_respuesta(login, 80)
    if res.pop("sigue", False) or res["paso"] == "codigo":
        res["login_id"] = login_id
    else:
        with _lock:
            _pendientes.pop(login_id, None)
    return res
