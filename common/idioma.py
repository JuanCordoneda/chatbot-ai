"""Idioma del CONTENIDO que el sistema produce alrededor de un post.

Es UNA sola definición para los dos lugares que generan texto sobre el post:
la descripción visual de la imagen (visión, en ai_generator) y la transcripción
del video (whisper, en post_processor). Antes cada uno tenía su idioma metido a
mano — la descripción fija en español y whisper con fallback "es" — y con
cuentas de habla inglesa quedaba todo mezclado: el vendedor leía la descripción
en español, los comentarios salían en inglés, y el modelo copiaba palabras
sueltas en español dentro de comentarios en inglés.

OJO: esto NO es el idioma de la interfaz ni de los mensajes de error ("no se
pudo describir la imagen…"), que siguen en español porque el vendedor es
hispanohablante. Es el idioma del contenido.
"""

import os

# Código ISO. "en" de fábrica: las cuentas que trabajamos son de habla inglesa y
# los comentarios salen en inglés. Se cambia por env var sin tocar código.
IDIOMA_CONTENIDO = (os.environ.get("IDIOMA_CONTENIDO", "en").strip().lower() or "en")

# Nombre en español del idioma, para escribirlo DENTRO de los prompts (que están
# redactados en español). Un código desconocido se usa tal cual: como instrucción
# ("Describí en nl") el modelo igual lo entiende.
NOMBRE = {"en": "inglés", "es": "español", "pt": "portugués",
          "fr": "francés", "it": "italiano", "de": "alemán"}.get(
              IDIOMA_CONTENIDO, IDIOMA_CONTENIDO)

# Encabezado de la línea de cierre en las descripciones de carrusel/video. Va en
# el mismo idioma que la descripción: si no, quedaba un "En conjunto:" suelto
# arriba de un texto en inglés.
ETIQUETA_CONJUNTO = {"en": "Overall:", "es": "En conjunto:", "pt": "No conjunto:",
                     "fr": "Dans l'ensemble:", "it": "Nel complesso:",
                     "de": "Insgesamt:"}.get(IDIOMA_CONTENIDO, "En conjunto:")

# whisper solo sabe traducir HACIA el inglés (task="translate"). Si el idioma de
# contenido es inglés, un video en otro idioma se entrega ya traducido; con
# cualquier otro idioma de contenido no hay traducción posible y la transcripción
# vuelve a ser literal, en el idioma que se habla en el video.
TRADUCE_TRANSCRIPCION = IDIOMA_CONTENIDO == "en"
