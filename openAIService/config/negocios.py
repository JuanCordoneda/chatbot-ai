"""Catálogo de negocios para el bot multi-rubro.

Cada negocio define los datos que se inyectan en el system prompt. La estructura
es siempre la misma; lo único que cambia son los valores:

    {
        "nombre": str,    # cómo se llama el lugar
        "rubro": str,     # el rubro, redactado para encajar en "atiende el WhatsApp de {nombre}, {rubro}"
        "horarios": str,  # horarios en una línea, en lenguaje natural
        "carta": str,     # id de la carta genérica del rubro (ver CARTAS)
    }

Los precios y servicios NO viven en el negocio: son una carta genérica por rubro
(CARTAS) que el bot consulta con la herramienta consultar_carta. Así todos los
bares comparten la misma carta de bar, todas las peluquerías la de peluquería, etc.

El negocio de cada conversación lo elige el cliente desde WhatsApp con el menú
(ver build_menu / resolver_seleccion).
"""

DEFAULT_NEGOCIO_ID = "bar_luna"

NEGOCIOS = {
    "bar_luna": {
        "nombre": "Bar Luna",
        "rubro": "un bar",
        "horarios": "lunes a viernes de 18 a 02, sábados y domingos de 16 a 03",
        "carta": "bar",
    },
    "clinica_bella": {
        "nombre": "Clínica Bella",
        "rubro": "una clínica estética",
        "horarios": "lunes a viernes de 9 a 19, sábados de 9 a 13",
        "carta": "clinica_estetica",
    },
    "peluqueria_style": {
        "nombre": "Style Peluquería",
        "rubro": "una peluquería",
        "horarios": "martes a sábado de 10 a 20",
        "carta": "peluqueria",
    },
    "restaurante_donpepe": {
        "nombre": "Don Pepe",
        "rubro": "un restaurante",
        "horarios": "todos los días de 12 a 15:30 y de 20 a 00",
        "carta": "restaurante",
    },
    "gimnasio_ironfit": {
        "nombre": "IronFit",
        "rubro": "un gimnasio",
        "horarios": "lunes a viernes de 7 a 23, sábados de 9 a 18",
        "carta": "gimnasio",
    },
}

# Carta genérica por rubro. Cada carta tiene:
#   "servicios": lista de dicts {nombre, precio, descripcion}
#   "preguntas_frecuentes": lista de dicts {pregunta, respuesta}
# Es lo que devuelve la herramienta consultar_carta para que el bot responda con
# datos reales (precios, detalles y dudas habituales) en vez de improvisar.
CARTAS = {
    "bar": {
        "servicios": [
            {"nombre": "Cerveza artesanal (tercio)", "precio": "6 €",
             "descripcion": "Cerveza local de barril, en variedades rubia, tostada e IPA."},
            {"nombre": "Copa de vino", "precio": "8 €",
             "descripcion": "Selección de tintos, blancos y rosados de la isla y la península. Pregunta por el vino del día."},
            {"nombre": "Cóctel de autor", "precio": "14 €",
             "descripcion": "Combinados preparados por nuestro bartender. El más pedido es el Mojito Luna con hierbabuena fresca."},
            {"nombre": "Gin-tonic premium", "precio": "12 €",
             "descripcion": "Amplia selección de ginebras nacionales e internacionales con su tónica recomendada."},
            {"nombre": "Refresco, agua o zumo natural", "precio": "3,50 €",
             "descripcion": "Refrescos, aguas minerales y zumos naturales del día."},
            {"nombre": "Tabla de embutidos ibéricos", "precio": "22 €",
             "descripcion": "Jamón ibérico, lomo, chorizo y queso curado con pan con tomate. Para compartir entre dos o tres."},
            {"nombre": "Tabla de quesos", "precio": "18 €",
             "descripcion": "Cinco quesos artesanos con frutos secos y mermelada casera."},
            {"nombre": "Tapas variadas", "precio": "desde 5 €",
             "descripcion": "Croquetas caseras, patatas bravas, boquerones y más. Pregunta por las tapas del día."},
        ],
        "preguntas_frecuentes": [
            {"pregunta": "¿Hace falta reservar?",
             "respuesta": "Para tomar algo no hace falta, pero los fines de semana recomendamos reservar mesa, sobre todo si venís en grupo."},
            {"pregunta": "¿Tenéis terraza?",
             "respuesta": "Sí, tenemos terraza exterior con vistas. En temporada alta se llena pronto."},
            {"pregunta": "¿Se puede pagar con tarjeta?",
             "respuesta": "Sí, aceptamos tarjeta, contactless y Bizum."},
            {"pregunta": "¿Hay música en vivo?",
             "respuesta": "Los jueves y viernes tenemos DJ o música en directo a partir de las 22:00."},
            {"pregunta": "¿Tenéis opciones sin alcohol?",
             "respuesta": "Sí, preparamos cócteles sin alcohol, zumos naturales y cerveza 0,0."},
            {"pregunta": "¿Admitís grupos grandes?",
             "respuesta": "Sí, para grupos de más de 8 personas escríbenos con antelación y lo organizamos."},
        ],
    },
    "clinica_estetica": {
        "servicios": [
            {"nombre": "Valoración inicial", "precio": "gratis",
             "descripcion": "Primera consulta con nuestro equipo médico para estudiar tu caso y proponerte un plan personalizado. Dura unos 30 min y no tiene compromiso."},
            {"nombre": "Limpieza facial profunda", "precio": "65 €",
             "descripcion": "Higiene facial completa con exfoliación, extracción e hidratación. Sesión de 60 min."},
            {"nombre": "Depilación láser (por zona)", "precio": "desde 40 €",
             "descripcion": "Láser de diodo con sistema de enfriamiento. Axilas o ingles desde 40 €; piernas completas desde 120 €."},
            {"nombre": "Aplicación de bótox", "precio": "250 €",
             "descripcion": "Toxina botulínica para arrugas de expresión (frente, entrecejo y patas de gallo). Resultado natural, sesión de 20 min."},
            {"nombre": "Relleno con ácido hialurónico", "precio": "desde 300 €",
             "descripcion": "Relleno de labios o surcos con ácido hialurónico de alta calidad. Efecto inmediato."},
            {"nombre": "Masaje descontracturante", "precio": "55 €",
             "descripcion": "Masaje terapéutico de 50 min para aliviar tensión y contracturas."},
            {"nombre": "Peeling facial químico", "precio": "80 €",
             "descripcion": "Renovación de la piel para manchas y marcas. Requiere valoración previa."},
            {"nombre": "Radiofrecuencia facial", "precio": "90 €",
             "descripcion": "Tratamiento reafirmante antiedad. Se recomienda realizarlo en varias sesiones."},
        ],
        "preguntas_frecuentes": [
            {"pregunta": "¿La primera consulta es gratuita?",
             "respuesta": "Sí, la valoración inicial es gratuita y sin compromiso."},
            {"pregunta": "¿El láser duele?",
             "respuesta": "Nuestro láser de diodo lleva sistema de enfriamiento, así que la molestia es mínima."},
            {"pregunta": "¿Cuántas sesiones de láser necesito?",
             "respuesta": "Depende de la zona y del tipo de piel, pero lo habitual son entre 6 y 8 sesiones."},
            {"pregunta": "¿Cuánto dura el efecto del bótox?",
             "respuesta": "Entre 4 y 6 meses de media; después se puede repetir."},
            {"pregunta": "¿Puedo tomar el sol después de un tratamiento?",
             "respuesta": "Con láser o peeling recomendamos evitar el sol y usar protección alta durante unos días."},
            {"pregunta": "¿Se puede pagar a plazos?",
             "respuesta": "Sí, ofrecemos financiación para tratamientos a partir de cierto importe. Te lo explicamos en la clínica."},
        ],
    },
    "peluqueria": {
        "servicios": [
            {"nombre": "Corte de pelo (mujer)", "precio": "25 €",
             "descripcion": "Corte personalizado con lavado y peinado incluido."},
            {"nombre": "Corte de pelo (caballero)", "precio": "18 €",
             "descripcion": "Corte a máquina o tijera, con arreglo de barba opcional."},
            {"nombre": "Coloración completa", "precio": "60 €",
             "descripcion": "Tinte de raíz a puntas con productos sin amoniaco. Dura hora y media aprox."},
            {"nombre": "Mechas / balayage", "precio": "desde 90 €",
             "descripcion": "Mechas, babylights o balayage según el efecto que busques. Incluye tratamiento y peinado."},
            {"nombre": "Brushing / peinado", "precio": "20 €",
             "descripcion": "Lavado, secado y peinado con acabado profesional."},
            {"nombre": "Peinado para eventos", "precio": "45 €",
             "descripcion": "Recogidos y peinados para bodas, comuniones o fiestas."},
            {"nombre": "Tratamiento de keratina", "precio": "70 €",
             "descripcion": "Alisado y nutrición profunda; deja el pelo liso y sin encrespamiento durante semanas."},
            {"nombre": "Recogido de novia (con prueba)", "precio": "120 €",
             "descripcion": "Incluye prueba previa y peinado el día del evento."},
        ],
        "preguntas_frecuentes": [
            {"pregunta": "¿Necesito cita previa?",
             "respuesta": "Sí, trabajamos con cita para atenderte sin esperas. Puedes reservar por aquí mismo."},
            {"pregunta": "¿Cuánto dura una coloración?",
             "respuesta": "Entre una hora y hora y media, según el largo y el tipo de color."},
            {"pregunta": "¿Puedo llevar una foto de referencia?",
             "respuesta": "¡Claro! Nos ayuda mucho a entender el resultado que buscas."},
            {"pregunta": "¿Hacéis peinados de novia a domicilio?",
             "respuesta": "Sí, para bodas ofrecemos servicio a domicilio; consúltanos disponibilidad y zona."},
            {"pregunta": "¿Qué productos usáis?",
             "respuesta": "Trabajamos con marcas profesionales y opciones sin amoniaco para cuidar tu cabello."},
        ],
    },
    "restaurante": {
        "servicios": [
            {"nombre": "Menú del día", "precio": "18 €",
             "descripcion": "Primero, segundo, postre y bebida. Disponible de lunes a viernes al mediodía."},
            {"nombre": "Entrantes para compartir", "precio": "desde 8 €",
             "descripcion": "Croquetas caseras, pulpo a la gallega, gambas al ajillo y ensaladas de temporada."},
            {"nombre": "Arroces y paella", "precio": "22 € por persona",
             "descripcion": "Paella valenciana, arroz negro o del senyoret. Mínimo dos personas, se encarga con antelación."},
            {"nombre": "Pescado del día", "precio": "según mercado",
             "descripcion": "Pescado fresco de la lonja de Ibiza, a la plancha o a la sal. Pregunta por la pieza del día."},
            {"nombre": "Entrecot de ternera", "precio": "28 €",
             "descripcion": "Entrecot a la brasa con guarnición, al punto que prefieras."},
            {"nombre": "Pasta casera", "precio": "16 €",
             "descripcion": "Pasta fresca elaborada en casa con salsas a elegir."},
            {"nombre": "Postres caseros", "precio": "8 €",
             "descripcion": "Tarta de queso, flan de la casa y helados artesanos."},
            {"nombre": "Carta de vinos", "precio": "desde 16 € la botella",
             "descripcion": "Vinos de la isla, D.O. nacionales y espumosos."},
        ],
        "preguntas_frecuentes": [
            {"pregunta": "¿Hace falta reservar?",
             "respuesta": "Recomendamos reservar, sobre todo para cenas de fin de semana y en temporada alta."},
            {"pregunta": "¿Tenéis opciones vegetarianas o veganas?",
             "respuesta": "Sí, tenemos varios platos vegetarianos y podemos adaptar opciones veganas."},
            {"pregunta": "¿Tenéis platos sin gluten?",
             "respuesta": "Sí, disponemos de alternativas sin gluten; avísanos de cualquier alergia al reservar."},
            {"pregunta": "¿Se puede comer en la terraza?",
             "respuesta": "Sí, tenemos terraza; puedes indicarlo al reservar según disponibilidad."},
            {"pregunta": "¿Hacéis celebraciones o comidas de grupo?",
             "respuesta": "Sí, organizamos eventos con menú cerrado. Escríbenos y lo preparamos a tu medida."},
            {"pregunta": "¿Tenéis menú infantil?",
             "respuesta": "Sí, disponemos de menú infantil por 10 €."},
        ],
    },
    "gimnasio": {
        "servicios": [
            {"nombre": "Matrícula de alta", "precio": "30 €",
             "descripcion": "Pago único al inscribirte; incluye la valoración física inicial."},
            {"nombre": "Cuota mensual", "precio": "45 €",
             "descripcion": "Acceso libre a la sala de musculación y cardio y a todas las clases dirigidas."},
            {"nombre": "Bono trimestral", "precio": "120 €",
             "descripcion": "Tres meses de acceso completo con descuento frente a la cuota mensual."},
            {"nombre": "Pase de día", "precio": "12 €",
             "descripcion": "Acceso puntual de un día para quien no es socio."},
            {"nombre": "Clases dirigidas", "precio": "incluidas en la cuota",
             "descripcion": "Spinning, body pump, yoga, pilates y HIIT. Consulta el horario semanal."},
            {"nombre": "Entrenador personal", "precio": "35 € la sesión",
             "descripcion": "Sesión individual con plan a medida. Bonos de 5 y 10 sesiones con descuento."},
            {"nombre": "Evaluación física", "precio": "gratis para socios",
             "descripcion": "Medición de composición corporal y objetivos, cada trimestre."},
            {"nombre": "Acceso a sauna", "precio": "incluido",
             "descripcion": "Zona de sauna y ducha disponible para todos los socios."},
        ],
        "preguntas_frecuentes": [
            {"pregunta": "¿Hay permanencia?",
             "respuesta": "No, la cuota mensual no tiene permanencia; puedes darte de baja cuando quieras avisando con 15 días."},
            {"pregunta": "¿Las clases están incluidas?",
             "respuesta": "Sí, todas las clases dirigidas están incluidas en la cuota mensual."},
            {"pregunta": "¿Puedo congelar la cuota si me voy de vacaciones?",
             "respuesta": "Sí, puedes congelarla hasta un mes al año sin coste."},
            {"pregunta": "¿Ofrecéis clase de prueba?",
             "respuesta": "Sí, puedes venir a una clase o entrenamiento de prueba gratuito. Escríbenos para reservar día."},
            {"pregunta": "¿Cuál es el horario?",
             "respuesta": "Abrimos de lunes a viernes de 7 a 23 y sábados de 9 a 18."},
            {"pregunta": "¿Tenéis vestuarios y duchas?",
             "respuesta": "Sí, contamos con vestuarios, duchas y taquillas."},
        ],
    },
}


def get_negocio(negocio_id: str | None = None) -> dict:
    """Devuelve el negocio identificado por negocio_id.

    Si no se pasa id, usa DEFAULT_NEGOCIO_ID. Lanza ValueError si el id no existe,
    para fallar rápido al arrancar en vez de servir un prompt vacío.
    """
    if not negocio_id:
        negocio_id = DEFAULT_NEGOCIO_ID
    negocio = NEGOCIOS.get(negocio_id)
    if negocio is None:
        opciones = ", ".join(NEGOCIOS)
        raise ValueError(
            f"NEGOCIO_ID '{negocio_id}' no existe. Opciones disponibles: {opciones}"
        )
    return negocio


def get_carta(negocio: dict) -> dict:
    """Carta del negocio: servicios detallados y preguntas frecuentes.

    Devuelve la estructura {servicios, preguntas_frecuentes} del rubro para que
    el bot responda con datos reales. Si el rubro no tiene carta cargada, devuelve
    listas vacías con una nota controlada.
    """
    carta = CARTAS.get(negocio.get("carta"))
    if not carta:
        return {
            "servicios": [],
            "preguntas_frecuentes": [],
            "nota": "No hay carta cargada para este lugar; indícale al cliente que llame al local.",
        }
    return carta


def build_menu() -> str:
    """Texto del menú de selección que se manda por WhatsApp."""
    lineas = ["¡Hola! ¿Con cuál quieres hablar? Responde con el número:", ""]
    for i, negocio in enumerate(NEGOCIOS.values(), start=1):
        lineas.append(f"{i}. {negocio['nombre']} - {negocio['rubro']}")
    lineas.append("")
    lineas.append('Escribe "menú" en cualquier momento para volver a elegir.')
    return "\n".join(lineas)


def resolver_seleccion(texto: str) -> str | None:
    """Interpreta la respuesta del cliente al menú.

    Acepta el número de la opción (1, 2, ...) o el id exacto del negocio.
    Devuelve el negocio_id elegido, o None si no coincide con ninguna opción.
    """
    t = texto.strip().lower()
    ids = list(NEGOCIOS)
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < len(ids):
            return ids[idx]
        return None
    if t in NEGOCIOS:
        return t
    return None
