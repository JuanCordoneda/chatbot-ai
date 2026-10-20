"""Bot de la campaña Midi Toloache: les ofrece el descuento del mediodía a los que trabajan en el barrio.

Flujo de cada mensaje entrante (lo llama /getresponsegpt):
  1. Se guarda el mensaje del cliente.
  2. Si escribe STOP, se lo da de baja y no se le escribe más.
  3. Si el bot está pausado para ese prospecto (alguien del equipo tomó la
     charla, o el cliente dijo que no), no se responde.
  4. Si no, Claude arma la respuesta con el historial guardado. Mientras
     conversa usa `actualizar_ficha` para anotar lo que averigua y
     `derivar_a_persona` cuando hace falta alguien del equipo.
  5. Se guarda la respuesta del bot y se devuelve para mandarla por WhatsApp.
"""

import json
import logging
import os
import re

import anthropic

import db

log = logging.getLogger("prospector")

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Lo que el bot tiene que averiguar, en orden. El último paso es ofrecer la
# reserva: si dice que sí, sigue una persona del equipo.
PASOS = [
    ("interes", "Interés"),
    ("personas", "Personas"),
    ("reserva", "Reserva"),
]

# Desde cuántas personas el grupo pasa a una persona para ofrecerle La Playita.
GRUPO_GRANDE = 8

# Texto de la plantilla aprobada en Meta para el primer mensaje. Tiene que
# coincidir con la plantilla real: se usa para guardar en la conversación lo
# que efectivamente le llegó al cliente. {{1}} es el saludo (ver saludo()).
PLANTILLA_NOMBRE = os.environ.get("PROSPECT_TEMPLATE", "midi_toloache")
PLANTILLA_IDIOMA = os.environ.get("PROSPECT_TEMPLATE_LANG", "fr")
PLANTILLA_TEXTO = os.environ.get(
    "PROSPECT_TEMPLATE_TEXT",
    "Bonjour {{1}} ! Ici Toloache, le resto mexicain du 49 rue de Clignancourt (M° Château Rouge). "
    "Pour ceux qui travaillent dans le quartier, on fait -10 % sur la carte le midi, du mardi au vendredi "
    "de 12h à 15h, jusqu'au 18 décembre. Je vous envoie l'offre pour vous et vos collègues ?",
)
# Imagen de la cabecera de la plantilla, dentro de midi/. Vacío si la plantilla no lleva imagen.
PLANTILLA_IMAGEN = os.environ.get("PROSPECT_TEMPLATE_IMAGE", "toloache-midi-10.jpg")

STOP = re.compile(r"\s*(stop|arr[eê]te[rz]?|arr[eê]t|d[ée]sinscri\w*|unsubscribe)\s*[.!]*\s*", re.I)
MENSAJE_BAJA = "C'est noté, vous ne recevrez plus nos messages. Bonne journée !"


def primer_nombre(p: dict) -> str:
    return (p.get("nombre") or "").strip().split(" ")[0]


def _de(empresa: str) -> str:
    """'de' + nombre del comercio, con la contracción del francés (du Trianon, des Abbesses, de La Cigale)."""
    for art, contraccion in (("le ", "du "), ("les ", "des ")):
        if empresa.lower().startswith(art):
            return contraccion + empresa[len(art):]
    return "de " + empresa


def saludo(p: dict) -> str:
    """Lo que va después de 'Bonjour': el nombre de la persona o, si no lo tenemos, el equipo del comercio."""
    if primer_nombre(p):
        return primer_nombre(p)
    empresa = (p.get("empresa") or "").strip()
    if empresa:
        return "à l'équipe " + _de(empresa)
    return "à toute l'équipe"


def texto_plantilla(p: dict) -> str:
    return PLANTILLA_TEXTO.replace("{{1}}", saludo(p))


def paso_actual(ficha: dict) -> int:
    """Cuántos pasos de la charla ya están completos en la ficha."""
    return sum(1 for k, _ in PASOS if ficha.get(k) not in (None, "", 0))


def _estado_charla(clave: str) -> tuple[int, bool]:
    """Cuántas preguntas hizo el bot (sin contar la plantilla) y si ya ofreció la reserva."""
    msgs = db.mensajes(clave)
    del_bot = [m["texto"] for m in msgs if m["de"] == "bot"]
    if msgs and msgs[0]["de"] == "bot":
        del_bot = del_bot[1:]  # el primero es la plantilla
    ofrecio = any("midi toloache" in t.lower() or "réserve" in t.lower() for t in del_bot)
    return len(del_bot), ofrecio


HABLA_DE_PERSONAS = re.compile(r"\d|personne|coll[eè]gue|on est|nous sommes|seul|tout seul|deux|trois|quatre|cinq|six|sept|huit|dix", re.I)


def _paso_siguiente(preguntas: int, ofrecio: bool, ultimo_texto: str = "") -> str:
    if ofrecio:
        return ("Ya le confirmaste la oferta y le ofreciste reservar. No hagas ninguna pregunta más: interpretá su "
                "respuesta (sí, no o una duda), anotala y cerrá en una frase.")
    if preguntas >= 1:
        return ("Ya hiciste la primera pregunta. Tu mensaje ahora TIENE que ser la confirmación de la oferta con el "
                "ofrecimiento de reservar (pregunta 2), aunque la respuesta esté incompleta. No repreguntes nada.")
    if HABLA_DE_PERSONAS.search(ultimo_texto or ""):
        return ("Todavía no hiciste ninguna pregunta, pero ya te dijo cuántos son o algo parecido. Tu mensaje ahora "
                "TIENE que ser la confirmación de la oferta con el ofrecimiento de reservar (pregunta 2).")
    return "Todavía no hiciste ninguna pregunta: si quiere la oferta, mandá la pregunta 1."


def _system_prompt(p: dict, aj: dict, preguntas: int = 0, ofrecio: bool = False, ultimo_texto: str = "",
                   enlace: str = "") -> str:
    nombre = p.get("nombre") or "sin nombre todavía"
    empresa = f", de {p['empresa']}" if p.get("empresa") else ""
    link = (f"\nSi te pide más información o para reenviarla a sus compañeros, pasale este link con la oferta: {enlace}"
            if enlace else "")
    return f"""Respondés el WhatsApp de Toloache, un restaurante mexicano de París, para la campaña de mediodía "Midi Toloache".

La oferta, tal cual (no prometas nada que no esté acá): {aj['oferta']}
Zona de la campaña: {aj['zona']}.{link}

Estás hablando con alguien que trabaja en el barrio ({nombre}{empresa}). Ya le mandamos un primer mensaje con la oferta (-10 % en la carta al mediodía, de martes a viernes) preguntando si le mandábamos la oferta para él y sus compañeros. Tu objetivo es que venga a almorzar: saber cuántos son y ofrecerle reservar una mesa.

La charla tiene que ser cortísima: hacés como máximo DOS preguntas en total.
1. Primera pregunta, si dice que sí: preguntale cuántos son, más o menos, para almorzar por la zona. Solo si la oferta dice que vale para llevar ("à emporter"), sumá si prefieren comer en el local o para llevar. Por ejemplo: "Super ! Vous êtes combien à déjeuner dans le coin, à peu près ?"
2. Segunda pregunta: con lo que haya contestado, confirmale cómo se usa la oferta y ofrecele reservar una mesa un día de esta semana. Por ejemplo: "Parfait ! Montrez ce message ou dites « Midi Toloache » : -10 % pour chacun, -15 % dès 4. Je vous réserve une table un jour cette semaine ?"
Si en su respuesta ya dice cuántos son, no hagas la primera pregunta: andá directo a la segunda.

Cada vez que el cliente te dé un dato, llamá a actualizar_ficha antes de contestar.

Según lo que conteste:
- Si quiere la oferta: anotá interes "si" y estado "calificado".
- Si quiere reservar: anotá reserva "si" y, si los dijo, el día y la hora en dia_reserva. Usá derivar_a_persona con el día, la hora y cuántos son, y cerrá en una frase diciendo que alguien del equipo le confirma la reserva por acá. No confirmes vos la reserva.
- Si no quiere reservar por ahora: anotá reserva "no", recordale en una frase que alcanza con mostrar el mensaje o decir « Midi Toloache » y que la oferta vale hasta el 18 de diciembre.
- Si son {GRUPO_GRANDE} o más, o habla de un afterwork, un evento, un cumpleaños o la fiesta de fin de año: anotalo en evento y usá derivar_a_persona para que le ofrezcan privatizar La Playita (hasta 100 personas). Decile que alguien del equipo le escribe para organizarlo.
- Si dice "Non merci", que no le interesa o que no le escriban más: anotá interes "no" y estado "descartado", agradecé en una frase y no insistas.

Si pregunta algo que la oferta no dice (precios exactos de un plato, alergias, si vale para llevar o con alcohol cuando la oferta no lo aclara, otros horarios): no inventes nada. Usá derivar_a_persona y decile que alguien del equipo le contesta.
Si pide hablar con una persona: usá derivar_a_persona.
No prometas tiempos (por ejemplo "dans quelques minutes" o "aujourd'hui"): decí solamente que alguien del equipo le escribe por acá.
Si te pregunta directamente si sos un bot, decile la verdad: sos el asistente automático de Toloache y alguien del equipo puede seguir la charla cuando quiera.
Si te manda audio, fotos u otra cosa que no podés ver, pedile con amabilidad que te lo escriba.

Estilo: en francés, tratando de vous, cálido y directo, como un mensaje de WhatsApp de una o dos frases. Sin listas, sin asteriscos, sin emojis salvo que el cliente los use. Si el cliente escribe en otro idioma, contestale en ese idioma.

Ficha actual del cliente: {json.dumps(p['ficha'], ensure_ascii=False)}

Dónde está esta charla: preguntas que ya hiciste: {preguntas} de 2. {_paso_siguiente(preguntas, ofrecio, ultimo_texto)}"""


TOOLS = [
    {
        "name": "actualizar_ficha",
        "description": (
            "Anota en la ficha del cliente los datos que dio en la conversación. Pasá solo los campos "
            "que averiguaste; los demás dejalos afuera. Usá 'estado' para marcar al cliente como "
            "calificado o descartado cuando corresponda."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interes": {"type": "string", "enum": ["si", "no"], "description": "Si quiere la oferta del mediodía."},
                "personas": {"type": "integer", "description": "Cuántos son para almorzar, más o menos."},
                "modalidad": {"type": "string", "enum": ["sur_place", "a_emporter", "les_deux"],
                              "description": "Si comerían en el local, para llevar o las dos cosas, solo si lo dijo."},
                "reserva": {"type": "string", "enum": ["si", "no"], "description": "Si quiere reservar una mesa."},
                "dia_reserva": {"type": "string", "description": "Día y hora de la reserva, como lo dijo el cliente."},
                "evento": {"type": "string", "description": "Si habla de un grupo grande, afterwork o fiesta: qué y para cuántos."},
                "zona": {"type": "string", "description": "Dónde trabaja (calle, barrio o estación)."},
                "nombre": {"type": "string", "description": "Nombre del cliente, si lo dijo y no lo teníamos."},
                "empresa": {"type": "string", "description": "Nombre de la empresa o comercio, si lo dijo y no lo teníamos."},
                "estado": {"type": "string", "enum": ["en_charla", "calificado", "descartado"]},
            },
            "required": [],
        },
    },
    {
        "name": "derivar_a_persona",
        "description": (
            "Avisa al equipo de Toloache que tiene que seguir una persona esta conversación y pausa las respuestas "
            "automáticas. Usala cuando el cliente quiere reservar, es un grupo grande o un evento, pregunta algo que "
            "la oferta no dice o pide hablar con una persona."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "description": "Qué necesita el cliente, en una frase para el equipo (con día, hora y cuántos son si es una reserva)."},
            },
            "required": ["motivo"],
        },
    },
]


def _ejecutar_herramienta(clave: str, nombre: str, datos: dict, efectos: dict) -> str:
    if nombre == "actualizar_ficha":
        ficha = db.actualizar_ficha(clave, datos)
        cambios = {}
        p = db.obtener(clave)
        for campo in ("nombre", "empresa"):
            v = (datos.get(campo) or "").strip()
            if v and not p.get(campo):
                cambios[campo] = v
        if datos.get("estado") in ("en_charla", "calificado", "descartado"):
            cambios["estado"] = datos["estado"]
            if datos["estado"] == "descartado":
                efectos["pausar"] = True
        if cambios:
            db.actualizar(clave, **cambios)
        return json.dumps({"ok": True, "ficha": ficha}, ensure_ascii=False)
    if nombre == "derivar_a_persona":
        db.actualizar(clave, atencion=(datos.get("motivo") or "El cliente necesita hablar con alguien del equipo").strip())
        efectos["pausar"] = True
        return json.dumps({"ok": True, "nota": "El equipo ya está avisado. Decile al cliente que alguien del equipo le escribe por acá."})
    return json.dumps({"error": f"Herramienta desconocida: {nombre}"})


def _historial(clave: str) -> list[dict]:
    """Arma los mensajes para Claude desde lo guardado: cliente = user, bot y equipo = assistant."""
    turnos: list[dict] = []
    for m in db.mensajes(clave, limite=40):
        rol = "user" if m["de"] == "cliente" else "assistant"
        texto = m["texto"] if m["de"] != "humano" else f"(Alguien del equipo escribió en persona) {m['texto']}"
        if turnos and turnos[-1]["role"] == rol:
            turnos[-1]["content"] += "\n" + texto
        else:
            turnos.append({"role": rol, "content": texto})
    # La conversación tiene que empezar con un turno del cliente.
    if turnos and turnos[0]["role"] == "assistant":
        turnos.insert(0, {"role": "user", "content": "(Todavía no escribí nada; ustedes me escribieron primero.)"})
    return turnos


def _motivo_derivacion(ficha: dict) -> str:
    """Qué tiene que hacer el equipo, armado desde la ficha (red de seguridad si el bot no derivó)."""
    personas = ficha.get("personas")
    if ficha.get("evento") or (personas or 0) >= GRUPO_GRANDE:
        detalle = ficha.get("evento") or f"grupo de {personas} personas"
        return f"Grupo o evento ({detalle}): ofrecerle privatizar La Playita"
    if ficha.get("reserva") == "si":
        partes = [ficha.get("dia_reserva") or "día a confirmar", f"{personas} pers." if personas else ""]
        return "Quiere reservar: " + " · ".join(x for x in partes if x)
    return ""


def responder(telefono: str, texto: str, enlace: str = "") -> str | None:
    """Procesa un mensaje del cliente. Devuelve el texto a mandarle, o None si no hay que responder.

    `enlace` es la URL pública de la página de la oferta (/midi); vacío si no se conoce.
    """
    p = db.obtener_o_crear(telefono)
    clave = p["clave"]
    db.agregar_mensaje(clave, "cliente", texto)

    if STOP.fullmatch(texto):
        # Baja pedida: no se le vuelve a escribir. Se confirma una sola vez.
        db.actualizar(clave, estado="descartado", bot_activo=False, atencion="")
        if p["estado"] == "descartado" and not p["bot_activo"]:
            return None
        db.agregar_mensaje(clave, "bot", MENSAJE_BAJA)
        return MENSAJE_BAJA

    if p["estado"] in ("nuevo", "contactado", "error"):
        db.actualizar(clave, estado="en_charla", ultimo_error="")

    if not p["bot_activo"]:
        log.info("Bot pausado para %s; el mensaje queda para el equipo.", clave)
        return None

    efectos: dict = {}
    motivo_antes = _motivo_derivacion(p["ficha"])
    preguntas, ofrecio = _estado_charla(clave)
    mensajes = _historial(clave)
    respuesta = ""
    for _ in range(6):
        p = db.obtener(clave)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_system_prompt(p, db.ajustes(), preguntas, ofrecio, texto, enlace),
            tools=TOOLS,
            messages=mensajes,
        )
        if resp.stop_reason == "tool_use":
            mensajes.append({"role": "assistant", "content": resp.content})
            resultados = []
            for bloque in resp.content:
                if bloque.type == "tool_use":
                    resultados.append({
                        "type": "tool_result",
                        "tool_use_id": bloque.id,
                        "content": _ejecutar_herramienta(clave, bloque.name, dict(bloque.input or {}), efectos),
                    })
            mensajes.append({"role": "user", "content": resultados})
            continue
        respuesta = "".join(b.text for b in resp.content if b.type == "text").strip()
        break

    p = db.obtener(clave)
    ficha = p["ficha"]
    # Red de seguridad: si quiere la oferta queda calificado; si recién pidió reservar o
    # resultó ser un grupo grande, se le avisa al equipo y el bot se pausa. Solo cuando
    # cambia, para que el equipo pueda volver a activar el bot.
    if ficha.get("interes") == "si" and p["estado"] not in ("calificado", "descartado"):
        db.actualizar(clave, estado="calificado")
    motivo = _motivo_derivacion(ficha)
    if motivo and motivo != motivo_antes:
        if not p["atencion"]:
            db.actualizar(clave, atencion=motivo)
        efectos["pausar"] = True

    if respuesta:
        db.agregar_mensaje(clave, "bot", respuesta)
    if efectos.get("pausar"):
        db.actualizar(clave, bot_activo=False)
    return respuesta or None
