"""
Navegador del server manejado a mano desde el panel (el celular).

El login automático (ig_login_nav) choca con lo que Instagram pone para frenar
bots: captcha, «confirmá que sos una persona». Eso lo tiene que resolver una
persona, y desde el celular no hay otra compu a mano. Acá el server abre el
mismo Chromium (mismo proxy, mismo User-Agent que el scraper) y el panel lo
muestra como una imagen: cada toque, texto o tecla del celular se reproduce en
la página y vuelve una captura nueva. La persona hace todo el login (contraseña,
código, captcha); el server solo mira las cookies, y cuando hay una sesión y la
página ya no está en una pantalla de verificación, avisa que hay candidata.

Cada sesión remota es un thread dueño de su navegador (Playwright sync no se
comparte entre threads); las órdenes llegan por una cola con su propia cola de
respuesta. Lo que se tipea no se loguea nunca: puede ser la contraseña.
"""
import base64
import queue
import secrets
import threading
import time

from modules import ig_login_nav as _nav

# Sin órdenes por este tiempo, se cierra: la persona se fue y un Chromium
# abierto son ~200 MB.
_TTL_INACTIVO = 8 * 60
# Pantalla con proporción de celular, para que en el panel se vea grande. Con
# el User-Agent de escritorio Instagram igual arma su versión angosta.
_VIEWPORT = {"width": 400, "height": 760}
_TRABADA = ("/challenge", "/auth_platform", "/two_factor", "/checkpoint",
            "/accounts/suspended", "/accounts/login")
_TECLAS = {"Enter", "Backspace", "Tab", "Escape"}

_sesiones: dict = {}
_lock = threading.Lock()


class _Remoto(threading.Thread):
    def __init__(self, username: str):
        super().__init__(daemon=True, name=f"ig_remoto:{username}")
        self.username = username
        self.ordenes: queue.Queue = queue.Queue()
        self.vivo = True
        # Después de un candidato rechazado no se vuelve a ofrecer la misma
        # sesión hasta que la persona haga algo en la página.
        self._esperar_accion = False

    def _log(self, que: str) -> None:
        print(f"[ig_remoto] @{self.username}: {que}", flush=True)

    def _cookies(self, ctx) -> dict:
        return {c["name"]: c["value"] for c in ctx.cookies()
                if c["domain"].endswith(_nav._DOMINIO) and c["value"]}

    def _foto(self, page) -> str:
        png = page.screenshot(type="jpeg", quality=60)
        return base64.b64encode(png).decode()

    def _hacer(self, page, orden: dict) -> None:
        tipo = orden.get("tipo")
        if tipo == "tap":
            page.mouse.click(float(orden["x"]), float(orden["y"]))
        elif tipo == "texto":
            page.keyboard.type(str(orden.get("texto", ""))[:200], delay=40)
        elif tipo == "tecla" and orden.get("tecla") in _TECLAS:
            page.keyboard.press(orden["tecla"])
        elif tipo == "scroll":
            page.mouse.wheel(0, max(-1500, min(1500, float(orden.get("dy", 0)))))
        elif tipo == "atras":
            page.go_back(wait_until="domcontentloaded", timeout=15000)
        elif tipo == "inicio":
            page.goto(f"{_nav._BASE}/accounts/login/", wait_until="domcontentloaded",
                      timeout=30000)
        else:
            return  # "mirar": solo la captura
        self._esperar_accion = False
        page.wait_for_timeout(1200)

    def _estado(self, page, ctx) -> dict:
        ruta = _nav._ruta(page.url)
        out = {"ruta": ruta}
        cookies = self._cookies(ctx)
        if (cookies.get("sessionid") and not self._esperar_accion
                and not any(t in page.url for t in _TRABADA)):
            # Unos segundos para que Instagram termine de setear el resto de
            # las cookies (ver ig_login_nav._asentar).
            page.wait_for_timeout(2500)
            out["cookies"] = self._cookies(ctx)
            self._esperar_accion = True
            self._log(f"sesión candidata en {ruta}")
        return out

    def run(self) -> None:
        from playwright.sync_api import sync_playwright
        try:
            with _nav._CUPO, sync_playwright() as pw:
                navegador = pw.chromium.launch(headless=True, proxy=_nav._proxy(),
                                               args=["--no-sandbox", "--disable-dev-shm-usage"])
                ctx = navegador.new_context(user_agent=_nav._UA, locale="es-AR",
                                            viewport=_VIEWPORT)
                page = ctx.new_page()
                self._log("abre el navegador")
                page.goto(f"{_nav._BASE}/accounts/login/", wait_until="domcontentloaded",
                          timeout=30000)
                while True:
                    try:
                        orden, respuesta = self.ordenes.get(timeout=_TTL_INACTIVO)
                    except queue.Empty:
                        self._log("cerrado por inactividad")
                        break
                    if orden is None:
                        break
                    try:
                        self._hacer(page, orden)
                        res = self._estado(page, ctx)
                        res["img"] = self._foto(page)
                    except Exception as e:
                        res = {"error": f"La página no respondió ({type(e).__name__}). "
                                        "Probá de nuevo."}
                        try:
                            res["img"] = self._foto(page)
                        except Exception:
                            pass
                    respuesta.put(res)
                navegador.close()
        except Exception as e:
            self._log(f"falló el navegador: {type(e).__name__}: "
                      f"{str(e).splitlines()[0][:200] if str(e) else ''}")
        finally:
            self.vivo = False
            # Quien esté esperando una respuesta no se queda colgado.
            while True:
                try:
                    _, respuesta = self.ordenes.get_nowait()
                except queue.Empty:
                    break
                if respuesta is not None:
                    respuesta.put({"cerrada": True})


def abrir(username: str) -> dict:
    username = (username or "").strip().lstrip("@").lower()
    if not username:
        return {"error": "Falta el @usuario."}
    if not _nav.disponible():
        return {"error": "El server no tiene el navegador instalado."}
    with _lock:
        for k in [k for k, v in _sesiones.items() if v.username == username]:
            _sesiones.pop(k).ordenes.put((None, None))
    r = _Remoto(username)
    r.start()
    rid = secrets.token_urlsafe(16)
    with _lock:
        _sesiones[rid] = r
    res = accion(rid, {"tipo": "mirar"}, espera=60)
    res["id"] = rid
    return res


def accion(rid: str, orden: dict, espera: float = 40) -> dict:
    with _lock:
        r = _sesiones.get(rid or "")
    if not r or not r.vivo:
        with _lock:
            _sesiones.pop(rid or "", None)
        return {"cerrada": True}
    respuesta: queue.Queue = queue.Queue()
    r.ordenes.put((orden, respuesta))
    try:
        res = respuesta.get(timeout=espera)
    except queue.Empty:
        return {"error": "El navegador tardó demasiado. Probá de nuevo."}
    res["username"] = r.username
    return res


def cerrar(rid: str) -> None:
    with _lock:
        r = _sesiones.pop(rid or "", None)
    if r:
        r.ordenes.put((None, None))
