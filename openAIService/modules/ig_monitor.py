"""
Monitor de la sesión de Instagram del scraper.

Hermano de growi_monitor, y existe por la misma razón con otro protagonista: el
sistema sabía que Instagram había rechazado la sesión mucho antes que nosotros.
El síntoma que ve el vendedor no dice "se cayó la sesión", dice nada: el post
sale igual, pero sin imagen y sin transcripción, porque el camino público
anónimo todavía trae el caption. Se descubría por un screenshot de alguien,
horas o días después. Pasó un miércoles y un sábado.

Dos decisiones que no son obvias:

1) CHEQUEA POCO Y EN HORARIO. Antes el healthcheck pegaba a /health/instagram
   cada 5 minutos y CADA UNO era una llamada autenticada real a Instagram: unas
   288 por día, desde una IP de datacenter, sin que nadie estuviera mirando un
   post. Ese es justo el patrón que hace que Instagram mande la cuenta a
   checkpoint. O sea: el chequeo que existía para detectar la caída era
   sospechoso de causarla. Ahora son 15 por día y solo en horario de trabajo
   argentino, que es cuando una caída le importa a alguien.

2) MIRA EL TRÁFICO REAL PRIMERO. Cada post que procesa un vendedor ya prueba la
   sesión gratis. Si hubo uno hace poco, el monitor no pregunta de nuevo: se
   ahorra la llamada. En un día con trabajo puede no hacer ni un chequeo propio
   y estar igual de informado.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from common import aviso

# Horario laboral argentino. Argentina no tiene horario de verano desde 2009,
# así que el offset fijo alcanza y evita depender de la tzdata del contenedor
# (que en las imágenes slim no siempre está).
_ART = timezone(timedelta(hours=-3))
DESDE = int(os.environ.get("IG_HEALTH_DESDE", "9"))    # 9:00 ART
HASTA = int(os.environ.get("IG_HEALTH_HASTA", "21"))   # 21:00 ART
CHEQUEOS_DIA = int(os.environ.get("IG_HEALTH_CHEQUEOS", "15"))

# Post público y estable que sirve de sonda. No tiene nada que ver con los
# clientes: solo hace falta que exista y sea de una cuenta que no vaya a
# borrarlo. El mismo que usa script_COOKIE.sh.
SHORTCODE = os.environ.get("IG_HEALTH_SHORTCODE", "DY5mFTuxsIO")

# Si sigue caído se recuerda cada tanto: sin esto, una caída del viernes a la
# tarde avisa una vez y se pierde entre los mensajes del fin de semana.
RECORDATORIO = float(os.environ.get("IG_HEALTH_REMINDER", "21600"))  # 6h

_estado = {
    "ok": None,              # None = todavía no se sabe
    "detalle": "sin chequear",
    "cuenta": "",            # con qué sesión se está scrapeando ahora
    "ultimo_chequeo": None,
    "caido_desde": None,
    "origen_dato": "",       # "chequeo" o "post real"
}
_lock = threading.Lock()
_ultimo_aviso = 0.0
_arrancado = False


def intervalo_seg() -> float:
    """Cada cuánto chequear DENTRO de la ventana, para que entren los chequeos
    del día. Con la ventana y el número por defecto: 12 h / 15 = cada 48 min."""
    horas = max(1, HASTA - DESDE)
    return (horas * 3600.0) / max(1, CHEQUEOS_DIA)


def en_horario(ahora: datetime = None) -> bool:
    a = (ahora or datetime.now(tz=_ART)).astimezone(_ART)
    return DESDE <= a.hour < HASTA


def segundos_hasta_apertura(ahora: datetime = None) -> float:
    """Cuánto falta para que abra la ventana. Fuera de horario el monitor duerme
    de verdad: a las 4 de la mañana no hay a quién avisarle, y cada pregunta de
    más a Instagram es una chance de que nos marquen como bot."""
    a = (ahora or datetime.now(tz=_ART)).astimezone(_ART)
    apertura = a.replace(hour=DESDE, minute=0, second=0, microsecond=0)
    if a.hour >= HASTA:
        apertura += timedelta(days=1)
    elif a.hour >= DESDE:
        return 0.0
    return max(0.0, (apertura - a).total_seconds())


def estado() -> dict:
    """Snapshot para /health/instagram y para el panel."""
    with _lock:
        e = dict(_estado)
    if e["caido_desde"]:
        e["caido_hace_min"] = round((time.time() - e["caido_desde"]) / 60)
    e["en_horario"] = en_horario()
    return e


def _texto_caida(detalle: str) -> str:
    return ("🔴 Instagram: se cayeron TODAS las sesiones del scraper.\n"
            f"Motivo: {detalle}\n"
            "Mientras tanto los posts salen sin imagen ni transcripción.\n"
            "Renovala desde el panel: /admin → pestaña Instagram.")


def registrar(ok: bool, detalle: str = "", origen: str = "",
              por_chequeo: bool = False, rotacion: str = "") -> None:
    """Anota el resultado de un uso de la sesión.

    Lo llama tanto el chequeo del monitor como CADA post real que procesa un
    vendedor (ver post_processor._registrar_uso_sesion). Es lo que hace que en
    un día de trabajo el monitor casi no necesite preguntar por su cuenta.

    `rotacion` es el nombre de la cuenta que Instagram acaba de rechazar, y solo
    lo manda quien VIO el rechazo. Antes esto se deducía de que cambiara la
    cuenta activa, y eso estaba mal: la cuenta activa también cambia cuando
    alguien carga una sesión de mayor prioridad desde el panel. Cargar dos
    cuentas disparó dos alertas de "Instagram tumbó X" sin que se cayera nada.
    """
    global _ultimo_aviso
    ahora = time.time()
    avisar_caida = avisar_vuelta = avisar_rotacion = None

    with _lock:
        antes_ok = _estado["ok"]
        antes_cuenta = _estado["cuenta"]
        _estado.update({
            "ok": bool(ok),
            "detalle": detalle or ("anduvo" if ok else "Instagram rechazó la sesión"),
            "ultimo_chequeo": ahora,
            "origen_dato": "chequeo" if por_chequeo else "post real",
        })
        if ok:
            _estado["cuenta"] = origen or antes_cuenta
            _estado["caido_desde"] = None
            if antes_ok is False:
                avisar_vuelta = _estado["cuenta"]
            # Rotó de cuenta y la nueva anda: no es una caída (el servicio sigue
            # en pie) pero hay que ir a arreglar la que quedó afuera, o el día
            # que se caiga esta no queda nada atrás.
            elif rotacion:
                avisar_rotacion = (rotacion, _estado["cuenta"])
        else:
            if _estado["caido_desde"] is None:
                _estado["caido_desde"] = ahora
            caido_hace = ahora - _estado["caido_desde"]
            if antes_ok is not False or (ahora - _ultimo_aviso) > RECORDATORIO:
                avisar_caida = (_estado["detalle"], caido_hace)
                _ultimo_aviso = ahora

    # Los avisos van FUERA del lock: mandar un WhatsApp tarda, y con el lock
    # tomado dejaba esperando a cualquier post que estuviera scrapeando.
    if avisar_caida:
        det, caido_hace = avisar_caida
        texto = _texto_caida(det)
        if caido_hace > 60:
            texto += f"\n(caída desde hace {round(caido_hace / 60)} min)"
        aviso.enviar(texto, "ig-health")
    elif avisar_vuelta:
        aviso.enviar(f"🟢 Instagram: la sesión volvió a andar ({avisar_vuelta}).", "ig-health")
    elif avisar_rotacion:
        vieja, nueva = avisar_rotacion
        aviso.enviar(f"⚠️ Instagram tumbó {vieja}; el scraper siguió con {nueva}, "
                     f"así que nadie se quedó sin servicio.\n"
                     f"Renová {vieja} cuando puedas desde /admin → Instagram: "
                     f"hasta entonces estamos sin respaldo.", "ig-health")


def chequear_ahora() -> dict:
    """Una prueba real contra Instagram. La usa el monitor y el endpoint de
    salud cuando se le pide a mano (?forzar=1), por ejemplo justo después de
    cargar una sesión nueva para ver si sirvió."""
    from modules.post_processor import _fetch_instagram_api
    try:
        r = _fetch_instagram_api(SHORTCODE)
    except Exception as e:
        # Un error nuestro no es "Instagram nos tiró": no despertamos a nadie
        # por un bug propio, se loguea y el estado queda como estaba.
        print(f"[ig-health] chequeo no concluyente: {e!r}", flush=True)
        return estado()

    ok = bool(r.get("owner_username"))
    # _fetch_instagram_api ya llamó a registrar() por cada sesión que probó. Se
    # vuelve a llamar solo para dejar asentado que este dato salió de un chequeo
    # y no de un post de verdad.
    registrar(ok, r.get("_error", "") if not ok else "", por_chequeo=True,
              origen=estado().get("cuenta", ""))
    return estado()


def _loop() -> None:
    while True:
        try:
            espera = segundos_hasta_apertura()
            if espera > 0:
                print(f"[ig-health] fuera de horario ({DESDE}-{HASTA} ART): "
                      f"duermo {round(espera / 60)} min", flush=True)
                time.sleep(min(espera, 3600))
                continue

            paso = intervalo_seg()
            with _lock:
                ultimo = _estado["ultimo_chequeo"] or 0
            # El tráfico real ya probó la sesión hace poco: no preguntamos de
            # nuevo. Este `continue` es el que convierte 15 chequeos diarios en
            # muchos menos los días con trabajo.
            if time.time() - ultimo < paso:
                time.sleep(min(paso - (time.time() - ultimo), 300))
                continue

            chequear_ahora()
        except Exception as e:
            print(f"[ig-health] error en el loop: {e!r}", flush=True)
            time.sleep(60)


def arrancar() -> None:
    global _arrancado
    if _arrancado:
        return
    _arrancado = True
    print(f"[ig-health] monitor de la sesión de Instagram: {CHEQUEOS_DIA} chequeos "
          f"por día entre las {DESDE} y las {HASTA} ART "
          f"(uno cada {round(intervalo_seg() / 60)} min), más lo que aporte el "
          f"tráfico real", flush=True)
    threading.Thread(target=_loop, daemon=True, name="ig-health").start()
