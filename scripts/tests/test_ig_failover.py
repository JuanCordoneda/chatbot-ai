"""
La sesión de Instagram: rotación de cuenta y cadencia del monitor.

Los dos comportamientos que existen para que no se repita lo del miércoles y el
sábado, cuando Instagram rechazó la cuenta del scraper y los vendedores
estuvieron generando posts sin imagen ni transcripción sin que nadie supiera:

  - si Instagram tumba una cuenta, el scrapeo sigue con la siguiente (y solo
    con eso: un rate limit NO quema el respaldo);
  - el monitor pregunta poco y en horario, porque el chequeo de antes —cada 5
    minutos, todo el día— era una llamada autenticada real y es sospechoso de
    haber causado los checkpoints que venía a detectar.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _ruta in ("/app", RAIZ, os.path.join(RAIZ, "openAIService")):
    if os.path.isdir(_ruta) and _ruta not in sys.path:
        sys.path.insert(0, _ruta)

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido

from common import repository as _repo                              # noqa: E402
from modules import ig_monitor                                      # noqa: E402
from modules import post_processor as pp                            # noqa: E402

fallas = []


def chequear(que, condicion, detalle=""):
    print(("  ok   " if condicion else "  FALLA") + f"  {que}" + (f" — {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallas.append(que)


# Las sesiones que "tiene cargadas el panel" y lo que se les fue marcando.
MARCAS = []


def _sesiones(*_a, **_kw):
    return [
        {"id": 1, "username": "principal", "cookies": {"sessionid": "1%3Aaaa"}},
        {"id": 2, "username": "respaldo", "cookies": {"sessionid": "2%3Abbb"}},
    ]


def _marcar(session_id, ok, detalle=""):
    MARCAS.append((session_id, ok, detalle))


_repo.ig_sessions_para_usar = _sesiones
_repo.marcar_ig_session = _marcar
os.environ.pop("INSTAGRAM_COOKIES_JSON", None)
os.environ.pop("INSTAGRAM_SESSION_B64", None)

AVISOS = []
ig_monitor.aviso.enviar = lambda texto, etiqueta="aviso": AVISOS.append(texto) or True


def correr(respuestas):
    """Corre un fetch con una respuesta guionada por cuenta."""
    MARCAS.clear()
    usadas = []

    def falso(shortcode, cookies):
        usadas.append(cookies["sessionid"])
        return respuestas[len(usadas) - 1]

    pp._fetch_instagram_api_con = falso
    pp._cuenta_avisada = ""
    return pp._fetch_instagram_api("XYZ"), usadas


print("\n1) Instagram rechaza la primera cuenta: el post sale igual con la segunda")
MUERTA = {"_error": "checkpoint", "_error_kind": "sesion_muerta"}
OK = {"owner_username": "vipsportslv", "caption": "hola"}
r, usadas = correr([MUERTA, OK])
chequear("el post se resuelve igual", r.get("owner_username") == "vipsportslv", str(r))
chequear("probó las dos cuentas, en orden", usadas == ["1%3Aaaa", "2%3Abbb"], str(usadas))
chequear("marcó caída la primera", (1, False, "checkpoint") in MARCAS, str(MARCAS))
chequear("marcó viva la segunda", (2, True, "") in MARCAS, str(MARCAS))

print("\n2) Un rate limit NO quema la cuenta de respaldo")
LIMITE = {"_error": "Instagram nos está limitando (rate limit)"}
r, usadas = correr([LIMITE, OK])
chequear("no rotó de cuenta", usadas == ["1%3Aaaa"], str(usadas))
chequear("no marcó nada", MARCAS == [], str(MARCAS))
chequear("devuelve el error tal cual", r.get("_error", "").startswith("Instagram nos"), str(r))

print("\n3) Se cayeron todas: se dice claro y se avisa una sola vez")
AVISOS.clear()
r, usadas = correr([MUERTA, MUERTA])
chequear("probó las dos", usadas == ["1%3Aaaa", "2%3Abbb"], str(usadas))
chequear("devuelve el último error", r.get("_error_kind") == "sesion_muerta", str(r))
chequear("avisó por WhatsApp", len(AVISOS) == 1, f"{len(AVISOS)} avisos")
chequear("el aviso dice dónde arreglarlo",
         AVISOS and "/admin" in AVISOS[0], AVISOS[0] if AVISOS else "")
antes = len(AVISOS)
correr([MUERTA, MUERTA])
chequear("no repite el aviso en cada post", len(AVISOS) == antes, f"{len(AVISOS)} avisos")

print("\n4) La cuenta vuelve: se avisa la recuperación")
AVISOS.clear()
correr([OK])
chequear("avisó que volvió", any("volvió" in a for a in AVISOS), str(AVISOS))

print("\n5) La env var sigue siendo el último recurso")
_repo.ig_sessions_para_usar = lambda *a, **k: []
os.environ["INSTAGRAM_COOKIES_JSON"] = '{"sessionid": "9%3Azzz"}'
cands = pp._sesiones_disponibles()
chequear("con la base vacía queda la del entorno",
         [c["origen"] for c in cands] == ["INSTAGRAM_COOKIES_JSON"], str(cands))
_repo.ig_sessions_para_usar = _sesiones
cands = pp._sesiones_disponibles()
chequear("con cuentas cargadas, la del entorno va ÚLTIMA",
         [c["origen"] for c in cands] == ["@principal", "@respaldo", "INSTAGRAM_COOKIES_JSON"],
         str([c["origen"] for c in cands]))
os.environ.pop("INSTAGRAM_COOKIES_JSON")

print("\n6) El monitor pregunta poco y en horario argentino")
from datetime import datetime, timedelta, timezone                  # noqa: E402
ART = timezone(timedelta(hours=-3))
chequear("15 chequeos entre las 9 y las 21 = uno cada 48 min",
         round(ig_monitor.intervalo_seg() / 60) == 48, f"{ig_monitor.intervalo_seg()}s")
chequear("a las 10 de la mañana chequea",
         ig_monitor.en_horario(datetime(2026, 9, 7, 10, 0, tzinfo=ART)))
chequear("a las 3 de la mañana no",
         not ig_monitor.en_horario(datetime(2026, 9, 7, 3, 0, tzinfo=ART)))
chequear("a las 22 tampoco",
         not ig_monitor.en_horario(datetime(2026, 9, 7, 22, 0, tzinfo=ART)))
chequear("a las 3 AM espera 6 h hasta que abra",
         round(ig_monitor.segundos_hasta_apertura(datetime(2026, 9, 7, 3, 0, tzinfo=ART)) / 3600) == 6)
chequear("a las 23 espera 10 h (abre mañana)",
         round(ig_monitor.segundos_hasta_apertura(datetime(2026, 9, 7, 23, 0, tzinfo=ART)) / 3600) == 10)
# 15 chequeos propios es el techo, no el piso: cada post real cuenta como
# prueba de vida y saltea el siguiente.
chequear("un día entero de chequeos son 15",
         round(((ig_monitor.HASTA - ig_monitor.DESDE) * 3600) / ig_monitor.intervalo_seg()) == 15)

print("\n" + ("TODO OK" if not fallas else f"{len(fallas)} FALLAS: {fallas}"))
sys.exit(1 if fallas else 0)
