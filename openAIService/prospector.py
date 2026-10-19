"""Bot prospector: entrevista a transportistas por WhatsApp y completa su ficha.

Flujo de cada mensaje entrante (lo llama /getresponsegpt):
  1. Se guarda el mensaje del cliente.
  2. Si el bot está pausado para ese prospecto (Ignacio tomó la charla, o el
     cliente pidió que no le escriban), no se responde.
  3. Si no, Claude arma la respuesta con el historial guardado. Mientras
     conversa usa `actualizar_ficha` para anotar lo que averigua y
     `derivar_a_ignacio` cuando hace falta una persona.
  4. Se guarda la respuesta del bot y se devuelve para mandarla por WhatsApp.
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
# propuesta de filtros: si dice que sí, sigue Ignacio en persona.
PASOS = [
    ("flota", "Flota"),
    ("quien_compra", "Compras / taller"),
    ("propuesta", "Propuesta"),
]

# Texto de la plantilla aprobada en Meta para el primer mensaje. Tiene que
# coincidir con la plantilla real: se usa para guardar en la conversación lo
# que efectivamente le llegó al cliente. {{1}} es el nombre del contacto.
PLANTILLA_NOMBRE = os.environ.get("PROSPECT_TEMPLATE", "prospeccion_filtros")
PLANTILLA_IDIOMA = os.environ.get("PROSPECT_TEMPLATE_LANG", "es_AR")
PLANTILLA_TEXTO = os.environ.get(
    "PROSPECT_TEMPLATE_TEXT",
    "Hola {{1}}, ¿cómo va? Soy del equipo de Ignacio, vendemos filtros para camiones y "
    "colectivos. ¿Te puedo hacer unas preguntas rápidas sobre tu flota?",
)


def primer_nombre(p: dict) -> str:
    return (p.get("nombre") or "").strip().split(" ")[0]


def texto_plantilla(p: dict) -> str:
    return PLANTILLA_TEXTO.replace("{{1}}", primer_nombre(p) or "qué tal")


def paso_actual(ficha: dict) -> int:
    """Cuántos pasos de la entrevista ya están completos en la ficha."""
    return sum(1 for k, _ in PASOS if ficha.get(k) not in (None, "", 0))


def _estado_charla(clave: str) -> tuple[int, bool]:
    """Cuántas preguntas hizo el bot (sin contar la plantilla) y si ya ofreció la propuesta."""
    msgs = db.mensajes(clave)
    del_bot = [m["texto"] for m in msgs if m["de"] == "bot"]
    if msgs and msgs[0]["de"] == "bot":
        del_bot = del_bot[1:]  # el primero es la plantilla
    ofrecio = any("propuesta" in t.lower() for t in del_bot)
    return len(del_bot), ofrecio


HABLA_DE_FLOTA = re.compile(r"cami[oó]n|camiones|colectiv|micro|[oó]mnibus|unidades|semi|chasis|\d", re.I)


def _paso_siguiente(preguntas: int, ofrecio: bool, ultimo_texto: str = "") -> str:
    if ofrecio:
        return ("Ya le ofreciste la propuesta. No hagas ninguna pregunta más: interpretá su respuesta "
                "(sí, no o una duda), anotala y cerrá en una frase.")
    if preguntas >= 1:
        return ("Ya hiciste la primera pregunta. Tu mensaje ahora TIENE que ser la oferta de la propuesta "
                "(pregunta 2), aunque la respuesta sobre la flota esté incompleta. No repreguntes nada de la flota.")
    if HABLA_DE_FLOTA.search(ultimo_texto or ""):
        return ("Todavía no hiciste ninguna pregunta, pero ya te contó algo de su flota. Tu mensaje ahora TIENE que "
                "ser la oferta de la propuesta (pregunta 2). No preguntes marcas ni nada más de la flota.")
    return "Todavía no hiciste ninguna pregunta: mandá la pregunta 1."


def _system_prompt(p: dict, aj: dict, preguntas: int = 0, ofrecio: bool = False, ultimo_texto: str = "") -> str:
    nombre = p.get("nombre") or "sin nombre todavía"
    empresa = f", de {p['empresa']}" if p.get("empresa") else ""
    return f"""Respondés el WhatsApp de prospección de Ignacio. Ignacio {aj['oferta']}. Zona: {aj['zona']}.

Estás hablando con alguien de una empresa de transporte ({nombre}{empresa}). Ya le mandamos un primer mensaje presentándonos y preguntando si podíamos hacerle unas preguntas sobre su flota. Tu objetivo es conocer su flota a grandes rasgos y ofrecerle que Ignacio le arme una propuesta de filtros. La persona ideal es quien maneja las compras o el taller.

La charla tiene que ser cortísima: hacés como máximo DOS preguntas en total.
1. Primera pregunta, abierta y en un solo mensaje: pedile que te cuente a grandes rasgos su flota (si son camiones o colectivos, más o menos cuántas unidades y de qué marcas) y si es quien ve las compras de filtros o el taller. Por ejemplo: "¡Buenísimo! Contame a grandes rasgos: ¿qué manejan, camiones o colectivos, más o menos cuántas unidades y de qué marcas? ¿Y vos ves las compras de filtros?"
2. Segunda pregunta: con lo que haya contestado, aunque esté incompleto, agradecé en pocas palabras y preguntale si quiere que Ignacio le arme una propuesta de filtros para su flota. Si no es quien compra y no te pasó el contacto, en ese mismo mensaje pedile con quién conviene hablar (nombre y celular).
Si en su respuesta ya aparece algo de la flota, aunque sea solo el tipo o la cantidad, no hagas la primera pregunta: andá directo a la segunda. Si todavía no sabés quién ve las compras, sumalo en esa misma pregunta. Nunca repreguntes detalles de la flota (marcas, cantidades, modelos): lo que falte lo completa Ignacio.
No digas qué productos, marcas o stock tenemos: eso lo habla Ignacio.
No prometas tiempos (por ejemplo "en unos minutos" o "hoy"): decí solamente que Ignacio le escribe en persona.
La pregunta 2 es corta, por ejemplo: "¡Gracias! ¿Querés que Ignacio te arme una propuesta de filtros para tu flota?" (si no sabés quién ve las compras, agregá "¿Y con quién hablo de compras?").

Cada vez que el cliente te dé un dato, llamá a actualizar_ficha antes de contestar.

Según la respuesta a la propuesta:
- Si dice que sí: anotá propuesta "si" y estado "calificado", usá derivar_a_ignacio con un resumen de la flota, y cerrá en una frase diciendo que Ignacio le escribe en persona.
- Si dice que no o que por ahora no: anotá propuesta "no" y estado "descartado", agradecé en una frase y dejale la puerta abierta para cuando necesite filtros.

Si dice que no le interesa o que no le escriban más: marcá estado "descartado", despedite con amabilidad en una frase y no insistas.
Si pregunta precios, stock, marcas de filtros, plazos de entrega o algo que no sabés: no inventes nada. Usá derivar_a_ignacio y decile que Ignacio le contesta eso personalmente.
Si quiere hacer un pedido o pide hablar con una persona: usá derivar_a_ignacio.
Si te pregunta directamente si sos un bot, decile la verdad: sos el asistente automático de Ignacio y él puede seguir la charla cuando quiera.
Si te manda audio, fotos u otra cosa que no podés ver, pedile con amabilidad que te lo escriba.

Estilo: español rioplatense con voseo, cordial y directo, como un mensaje de WhatsApp de una o dos frases. Sin listas, sin asteriscos, sin emojis salvo que el cliente los use.

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
                "flota": {"type": "string", "enum": ["camiones", "colectivos", "ambos", "otro"],
                          "description": "Qué tipo de unidades tiene."},
                "unidades": {"type": "integer", "description": "Cantidad de unidades andando."},
                "marcas": {"type": "string", "description": "Marcas de las unidades, como las dijo el cliente."},
                "quien_compra": {"type": "string", "description": "Quién maneja las compras o el taller, solo si el cliente lo dijo (por ejemplo 'él mismo', 'Marcela'). No lo supongas."},
                "contacto_compras": {"type": "string", "description": "Nombre y teléfono de la persona de compras o taller, si no es con quien hablás."},
                "propuesta": {"type": "string", "enum": ["si", "no"], "description": "Si quiere que Ignacio le arme una propuesta de filtros."},
                "zona": {"type": "string", "description": "Barrio, localidad o zona donde trabaja."},
                "nombre": {"type": "string", "description": "Nombre del cliente, si lo dijo y no lo teníamos."},
                "empresa": {"type": "string", "description": "Nombre de la empresa, si lo dijo y no lo teníamos."},
                "estado": {"type": "string", "enum": ["en_charla", "calificado", "descartado"]},
            },
            "required": [],
        },
    },
    {
        "name": "derivar_a_ignacio",
        "description": (
            "Avisa a Ignacio que tiene que seguir él esta conversación y pausa las respuestas automáticas. "
            "Usala cuando el cliente pregunta precios, stock o plazos, quiere hacer un pedido o pide hablar con una persona."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "description": "Qué necesita el cliente, en una frase para Ignacio."},
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
    if nombre == "derivar_a_ignacio":
        db.actualizar(clave, atencion=(datos.get("motivo") or "El cliente necesita hablar con Ignacio").strip())
        efectos["pausar"] = True
        return json.dumps({"ok": True, "nota": "Ignacio ya está avisado. Decile al cliente que Ignacio le escribe."})
    return json.dumps({"error": f"Herramienta desconocida: {nombre}"})


def _historial(clave: str) -> list[dict]:
    """Arma los mensajes para Claude desde lo guardado: cliente = user, bot e Ignacio = assistant."""
    turnos: list[dict] = []
    for m in db.mensajes(clave, limite=40):
        rol = "user" if m["de"] == "cliente" else "assistant"
        texto = m["texto"] if m["de"] != "humano" else f"(Ignacio escribió en persona) {m['texto']}"
        if turnos and turnos[-1]["role"] == rol:
            turnos[-1]["content"] += "\n" + texto
        else:
            turnos.append({"role": rol, "content": texto})
    # La conversación tiene que empezar con un turno del cliente.
    if turnos and turnos[0]["role"] == "assistant":
        turnos.insert(0, {"role": "user", "content": "(Todavía no escribí nada; ustedes me escribieron primero.)"})
    return turnos


def responder(telefono: str, texto: str) -> str | None:
    """Procesa un mensaje del cliente. Devuelve el texto a mandarle, o None si no hay que responder."""
    p = db.obtener_o_crear(telefono)
    clave = p["clave"]
    db.agregar_mensaje(clave, "cliente", texto)
    if p["estado"] in ("nuevo", "contactado", "error"):
        db.actualizar(clave, estado="en_charla", ultimo_error="")

    if not p["bot_activo"]:
        log.info("Bot pausado para %s; el mensaje queda para Ignacio.", clave)
        return None

    efectos: dict = {}
    preguntas, ofrecio = _estado_charla(clave)
    mensajes = _historial(clave)
    respuesta = ""
    for _ in range(6):
        p = db.obtener(clave)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_system_prompt(p, db.ajustes(), preguntas, ofrecio, texto),
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
    # Red de seguridad: si aceptó la propuesta, queda calificado y le avisa a Ignacio.
    if p["ficha"].get("propuesta") == "si" and p["estado"] != "calificado":
        db.actualizar(clave, estado="calificado")
    if p["ficha"].get("propuesta") == "si":
        if not p["atencion"]:
            db.actualizar(clave, atencion="Quiere una propuesta de filtros para su flota")
        efectos["pausar"] = True

    if respuesta:
        db.agregar_mensaje(clave, "bot", respuesta)
    if efectos.get("pausar"):
        db.actualizar(clave, bot_activo=False)
    return respuesta or None
