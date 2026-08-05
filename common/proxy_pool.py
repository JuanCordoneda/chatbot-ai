"""
Pool de proxies de salida para el CRM de Growi.

El CRM ata la sesión a la IP que se loguea y Railway no da IP de salida fija,
así que el tráfico se rutea por un proxy de IP fija que tiene que estar en la
whitelist del CRM.

Durante mucho tiempo ese proxy fue UNA sola IP en una env var. Cuando esa
máquina se cayó, no hubo a dónde caer: el sistema quedó sin poder publicar hasta
que un humano se diera cuenta, levantara la instancia y redeployara con la IP
nueva. Este módulo convierte esa env var en una LISTA con failover automático y
cooldown, para que un proxy muerto sea un bache de milisegundos y no una caída.

Formato de GROWI_HTTP_PROXY (retrocompatible: un valor solo sigue andando):

    http://user:pass@ip1:8888,http://ip2:3128

Se prueban en orden. El primero que responde gana.
"""
import threading
import time

# Cuánto queda marcado como muerto un proxy que falló. Corto a propósito: si se
# cayó por un reinicio de 30s no queremos descartarlo por media hora, y el costo
# de reintentar de más es solo un connect timeout.
COOLDOWN_DEFAULT = 180.0


class ProxyPool:
    """Lista ordenada de proxies con memoria de cuáles están caídos.

    Thread-safe: los jobs de generación corren en threads y comparten el pool.
    """

    def __init__(self, raw: str = "", cooldown: float = COOLDOWN_DEFAULT):
        self._proxies = self._parsear(raw)
        self._cooldown = cooldown
        self._muertos: dict[str, float] = {}   # proxy -> momento en que falló
        self._lock = threading.Lock()

    @staticmethod
    def _parsear(raw: str) -> list[str]:
        """Acepta separación por coma, punto y coma o espacios, y tolera espacios
        sueltos alrededor (pegar una lista en el panel de Railway suele dejarlos)."""
        if not raw:
            return []
        limpio = raw.replace(";", ",").replace("\n", ",").replace(" ", ",")
        vistos, orden = set(), []
        for p in limpio.split(","):
            p = p.strip()
            # Sin dedup, repetir una IP en la env var multiplicaría los intentos
            # contra la misma máquina caída antes de llegar a la que sí anda.
            if p and p not in vistos:
                vistos.add(p)
                orden.append(p)
        return orden

    @property
    def configurado(self) -> bool:
        """False = no hay proxies; se sale directo (dev local, o CRM sin whitelist)."""
        return bool(self._proxies)

    def candidatos(self) -> list[str | None]:
        """Proxies a probar, en orden: primero los vivos, después los que están
        en cooldown.

        Los muertos van al final en vez de excluirse: si TODOS están caídos,
        preferimos intentar igual y fallar con el error real del CRM antes que
        negarnos a trabajar por una marca que pusimos nosotros. Sin proxies
        configurados devuelve [None] = conexión directa.
        """
        if not self._proxies:
            return [None]
        ahora = time.monotonic()
        vivos, en_cooldown = [], []
        with self._lock:
            for p in self._proxies:
                caido = self._muertos.get(p)
                if caido is not None and ahora - caido < self._cooldown:
                    en_cooldown.append(p)
                else:
                    vivos.append(p)
        return vivos + en_cooldown

    def marcar_muerto(self, proxy: str | None) -> None:
        if not proxy:
            return
        with self._lock:
            self._muertos[proxy] = time.monotonic()

    def marcar_vivo(self, proxy: str | None) -> None:
        """Un proxy que respondió sale del cooldown al toque: no tiene sentido
        seguir mandándolo al fondo de la lista si ya demostró que anda."""
        if not proxy:
            return
        with self._lock:
            self._muertos.pop(proxy, None)

    def estado(self) -> list[dict]:
        """Para el healthcheck y los logs: qué hay configurado y qué está caído."""
        ahora = time.monotonic()
        with self._lock:
            return [{
                "proxy": _ofuscar(p),
                "vivo": not (p in self._muertos and ahora - self._muertos[p] < self._cooldown),
            } for p in self._proxies]


def _ofuscar(proxy: str) -> str:
    """Saca user:pass del proxy para poder loguearlo sin filtrar credenciales."""
    if "@" in proxy:
        esquema, _, resto = proxy.rpartition("@")
        prefijo = esquema.split("//")[0] + "//" if "//" in esquema else ""
        return f"{prefijo}***@{resto}"
    return proxy


def proxies_de(proxy: str | None) -> dict:
    """Traduce un proxy al dict que espera requests. None = conexión directa."""
    return {"http": proxy, "https": proxy} if proxy else {}
