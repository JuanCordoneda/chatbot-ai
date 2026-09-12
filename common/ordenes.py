"""
Armado de las órdenes que van al CRM (enviar_trafico.php).

Vive en `common/` porque lo necesitan los DOS servicios: el webService, que es
quien manda las órdenes del panel (comentarios y tráfico), y el openAIService,
que sigue teniendo el flujo del bot de WhatsApp. Antes esto estaba solo en
openAIService/modules/growi_client.py, y esa duplicación fue justamente el bug:
los comentarios se mandaban con las credenciales globales del .env y terminaban
imputados a otro vendedor.

Acá va únicamente lo que no toca la red: mezclar comentarios y pasar una orden
del formato del frontend al que espera el CRM.
"""
import random

# Encabezados que el CRM usa para saber el género de cada bloque de comentarios.
# La IA los emite como líneas sueltas dentro de la lista (ej: "mujeres:").
_HEADERS_GENERO = {"mujeres:", "hombres:"}


def es_header_genero(linea: str) -> bool:
    return (linea or "").strip().lower() in _HEADERS_GENERO


def mezclar_comentarios(comentarios: list) -> list:
    """
    Mezcla los comentarios seleccionados antes de enviarlos para que no se
    publiquen siempre en el orden en que la IA los generó.

    Si vienen segmentados por género (líneas "mujeres:" / "hombres:"), la salida
    queda SIEMPRE con un solo bloque por género y en este orden: "mujeres:" y
    sus comentarios, después "hombres:" y los suyos. Se baraja solo dentro de
    cada bloque, así el CRM sigue sabiendo de qué género es cada comentario.
    Antes los encabezados se dejaban fijos donde venían, y una lista con
    "mujeres: / hombres: / mujeres:" llegaba así al CRM (orden 3020069).
    De paso se tiran los comentarios repetidos (mismo texto, sin distinguir
    mayúsculas ni espacios): es el último filtro antes de publicar.

    Si no hay encabezados, baraja toda la lista sin sacar repetidos: es el caso
    del modo palabra clave, donde repetir la misma palabra ES el pedido.
    """
    comentarios = list(comentarios or [])
    if not any(es_header_genero(c) for c in comentarios):
        random.shuffle(comentarios)
        return comentarios

    sueltos: list = []   # antes del primer encabezado: sin género
    secciones = {"mujeres:": [], "hombres:": []}
    vistos: set = set()
    actual = sueltos
    for c in comentarios:
        if es_header_genero(c):
            actual = secciones[c.strip().lower()]
            continue
        clave = " ".join((c or "").split()).casefold()
        if not clave or clave in vistos:
            continue
        vistos.add(clave)
        actual.append(c)

    resultado: list = []
    random.shuffle(sueltos)
    resultado.extend(sueltos)
    for header, grupo in secciones.items():
        if grupo:
            random.shuffle(grupo)
            resultado.append(header)
            resultado.extend(grupo)
    return resultado


def normalizar_orden(o: dict, disponible: float, comentarios: list) -> dict:
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
        return mezclar_comentarios(orden.get("comentarios") or comentarios)

    def _tope(cant, coms):
        """Si mezclar_comentarios tiró repetidos, la orden no puede seguir
        pidiendo (y cobrando) la cantidad original."""
        reales = sum(1 for c in coms if not es_header_genero(c))
        try:
            return str(min(int(cant), reales)) if reales else str(cant)
        except (TypeError, ValueError):
            return cant

    if "url" in o:
        if o.get("tipo") == "comentarios":
            coms = _coms_de(o)
            o = {**o, "comentarios": coms}
            for campo in ("cantidad", "cant_inicial"):
                if campo in o:
                    o[campo] = _tope(o[campo], coms)
        return o

    cantidad = o.get("cantidad", 0)
    coms = _coms_de(o) if o.get("tipo") == "comentarios" else []
    if coms:
        cantidad = _tope(cantidad, coms)
    cuando = o.get("cuando", "ahora")
    programado = 1 if cuando not in ("ahora", None) else 0

    return {
        "redsocial_id": o.get("redsocialId") or o.get("redsocial_id"),
        "redsocial":    o.get("redsocial"),
        # Campo NUESTRO, no del CRM: lo usa el backend para preguntarle el precio
        # real a obtenercostotrafico.php y no confiar en el que mandó el
        # navegador. Se saca del payload justo antes de enviarlo.
        "producto_id":  o.get("productoId") or o.get("producto_id"),
        "prod":         o.get("productoNombre") or o.get("prod"),
        "demora":       " - ",
        "url":          o.get("link") or o.get("url") or "",
        "costo":        o.get("costo") or 0,
        "obs":          o.get("obs", ""),
        "cant_inicial": str(cantidad),
        "cantidad":     str(cantidad),
        "programado":   programado,
        "fecha_programada": o.get("fechaProgramada") or None,
        "comentarios":  coms,
        "disponible":   disponible,
    }


def log_comentarios_debug(nombre: str, coms: list) -> None:
    """Log de debug: muestra cómo quedó la lista de una orden de comentarios."""
    headers = [c for c in coms if es_header_genero(c)]
    if not headers:
        caso = "sin genero (lista plana)"
    elif len(headers) == 1:
        caso = f"solo {headers[0].strip().lower().rstrip(':')}"
    else:
        caso = "mixto (" + " + ".join(h.strip().lower().rstrip(':') for h in headers) + ")"
    print(f"[growi][debug] orden '{nombre}': caso {caso} | {len(coms)} lineas", flush=True)
    for i, c in enumerate(coms):
        marca = "  >>" if es_header_genero(c) else f"  {i:>3}"
        print(f"[growi][debug]{marca} {c}", flush=True)


def generar_informe(post_url: str, comentarios: list, *, insertadas: int = 0,
                    messages: list = None, errors: list = None,
                    error: str = None, ok: bool = False) -> str:
    """Informe de texto plano del envío. Mismo formato que el de siempre
    (openAIService/modules/reporter.py), pero sin depender de GrowiResult para
    que lo pueda usar también el webService.

    La fecha se toma en hora de Argentina: es un texto que lee el vendedor, y en
    UTC le mostraba una hora que no era la suya.
    """
    now = _ahora_ar().strftime("%d/%m/%Y %H:%M")

    if error:
        return (
            f"Informe de campaña — {now}\n"
            f"Post: {post_url}\n"
            f"Estado: ERROR\n"
            f"Detalle: {error}"
        )

    comentarios = list(comentarios or [])
    lineas_comentarios = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(comentarios))

    msgs_str = ""
    if messages:
        msgs_str = "\nMensajes del CRM:\n" + "\n".join(f"  {m}" for m in messages)

    errores_str = ""
    if errors:
        errores_str = "\nErrores:\n" + "\n".join(f"  - {e}" for e in errors)

    return (
        f"Informe de campaña — {now}\n"
        f"Post: {post_url}\n"
        f"Estado: {'OK' if ok else 'PARCIAL'}\n"
        f"Órdenes insertadas: {insertadas}\n"
        f"{msgs_str}"
        f"{errores_str}\n\n"
        f"Comentarios generados ({len(comentarios)}):\n{lineas_comentarios}"
    )


def _ahora_ar():
    """Hora local de Argentina (UTC-3). Los contenedores corren en UTC, así que
    `datetime.now()` pelado daba la hora equivocada — y con `date.today()` era
    peor: después de las 21:00 AR ya devolvía la fecha del día siguiente, que es
    exactamente por lo que las órdenes de la noche no aparecían en el gestor."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(tz=timezone(timedelta(hours=-3)))


def ahora_ar_texto() -> str:
    """Fecha y hora de Argentina, "YYYY-MM-DD HH:MM", que es el formato que el
    CRM espera en `fecha_programada`."""
    return _ahora_ar().strftime("%Y-%m-%d %H:%M")


def fecha_ar_hoy() -> str:
    """La fecha de HOY en Argentina, formato YYYY-MM-DD, para el campo `fecha`
    de la orden. Es el fallback de cuando no se puede consultar la hora al CRM."""
    return _ahora_ar().date().isoformat()
