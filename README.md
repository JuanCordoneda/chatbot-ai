# Midi Toloache (prospector de WhatsApp)

El bot les escribe por WhatsApp a comercios y oficinas a menos de 10 minutos de Toloache (49 rue de Clignancourt, 75018). Les ofrece el descuento del mediodía: -10 % en la carta, -15 % desde 4 personas, de martes a viernes de 12h a 15h, hasta el viernes 18 de diciembre. Todo se sigue en un panel web.

**Cómo se usa**

1. Entrá al panel: `https://<openai-service>/panel` (contraseña `PANEL_PASSWORD`; el usuario se deja vacío).
2. **Cargar lista**: pegá un contacto por línea (`Comercio, Teléfono` o `Nombre, Comercio, Teléfono`) o subí un CSV. Solo entran los celulares (06 y 07): los fijos no tienen WhatsApp y se descartan.
3. **Escribirles a los nuevos**: el bot manda la plantilla con la imagen, de a uno y como mucho 25 por día (`MAX_ENVIOS_DIA`). Al resto les escribe al día siguiente. Antes de cada tanda, revisá la calidad del número en WhatsApp Manager: si no está en verde, pará 2 o 3 días.
4. Cuando contestan, el bot sigue la charla en francés. Pregunta cuántos son, explica cómo se canjea la oferta («Midi Toloache») y ofrece reservar una mesa.
5. Si quieren reservar, son 8 o más (y se les ofrece La Playita) o preguntan algo que la oferta no dice, el bot pausa y lo marca como "Te necesita". Desde el panel les escribís vos o volvés a activar el bot.
6. Si alguien escribe STOP, queda descartado y no se le escribe más.

La página de la oferta para reenviar está en `https://<openai-service>/midi`. El bot la comparte cuando le piden más información.

**Antes de arrancar**

- Crear y aprobar en Meta (WhatsApp Manager › Plantillas) una plantilla de categoría Marketing llamada `midi_toloache`, en idioma francés (`fr`):
  - Cabecera: imagen (subí `openAIService/midi/toloache-midi-10.jpg` como ejemplo).
  - Cuerpo: `Bonjour {{1}} ! Ici Toloache, le resto mexicain du 49 rue de Clignancourt (M° Château Rouge). Pour ceux qui travaillent dans le quartier, on fait -10 % sur la carte le midi, du mardi au vendredi de 12h à 15h, jusqu'au 18 décembre. Je vous envoie l'offre pour vous et vos collègues ?`
  - Pie: `Répondez STOP pour ne plus recevoir nos messages`
  - Botones de respuesta rápida: `Oui, avec plaisir` y `Non merci`
  - En `{{1}}` el bot pone el nombre de la persona o, si solo tenemos el comercio, «à l'équipe de <comercio>».
- Variables del `openai-service`:
  - `PANEL_PASSWORD`: contraseña del panel (obligatoria en producción).
  - `PUBLIC_URL`: URL pública del `openai-service` (por ejemplo `https://<openai-service>`). Meta baja de ahí la imagen de la plantilla y el bot arma el link a `/midi`. Si no está, se toma del dominio con el que abrís el panel.
  - `WHATSAPP_SERVICE_URL`: URL del `whatsapp-service`.
  - `DB_PATH`: archivo de la base (por defecto `/data/prospector.db`). En Railway montá un volumen en `/data`; si no, la base se borra en cada deploy.
  - Opcionales: `MAX_ENVIOS_DIA` (por defecto 25), `PROSPECT_TEMPLATE`, `PROSPECT_TEMPLATE_LANG`, `PROSPECT_TEMPLATE_TEXT`, `PROSPECT_TEMPLATE_IMAGE` (archivo dentro de `midi/`; vacío si la plantilla no lleva imagen), `ANTHROPIC_MODEL` y `PAUSA_ENTRE_ENVIOS`.
- La oferta y la zona se cambian desde **Ajustes** en el panel. El bot no promete nada que no esté escrito ahí. Hoy dice «sur place, hors boissons alcoolisées». Si el dueño aprueba que valga para llevar o con alcohol, cambialo ahí y también en `openAIService/midi/index.html`.
- Las imágenes salen de `plantilla-midi/midi-10.html` y `midi-15.html`. Para regenerarlas, corré `plantilla-midi/build.sh` y copiá los `.jpg` a `openAIService/midi/`.

# Chatbot de WhatsApp con IA

Este proyecto implementa un chatbot para WhatsApp utilizando la API de WhatsApp Business, Flask para el backend, y la IA de OpenAI para el procesamiento de lenguaje natural.

## Arquitectura del Proyecto

El sistema se compone de varios componentes clave que trabajan en conjunto para proporcionar una experiencia interactiva a través de WhatsApp:

- Flask Server: Maneja las peticiones HTTP y sirve como el punto de conexión entre WhatsApp y el motor de IA.
- API de WhatsApp Business: Permite recibir y enviar mensajes a través de WhatsApp.
- OpenAI API: Se utiliza para generar respuestas inteligentes a partir de las preguntas recibidas.
- Google Cloud Storage: Almacena y gestiona los vectores de palabras para la IA.

Cuando un mensaje llega a través de WhatsApp, Flask procesa la solicitud, la pasa a la API de OpenAI para generar una respuesta y luego utiliza la API de WhatsApp Business para enviar esta respuesta al usuario.

![Arquitectura WhatsApp AI Chatbot](whatsapp-ai-chatbot-arquitectura.png)

### Requisitos Previos

Para ejecutar este proyecto necesitas:

- Una cuenta de Google Cloud para el almacenamiento de vectores.
- Una cuenta de desarrollador de Facebook con acceso a la API de WhatsApp Business.
- Acceso a la API de OpenAI.

### Configuración del Proyecto

Variables de Entorno: Configura las siguientes variables de entorno en tu sistema o en un archivo .env:

- WHATSAPP_ACCESS_TOKEN: Tu token de acceso para la API de WhatsApp Business.
- WHATSAPP_VERIFY_TOKEN: Tu token de verificación para la API de WhatsApp Business.
- WHATSAPP_API_URL: La URL de la API de WhatsApp Business.
- OPENAI_SERVICE_URL: La URL de tu servicio que interactúa con la API de OpenAI.
- GOOGLE_APPLICATION_CREDENTIALS: La ruta al archivo de credenciales de tu cuenta de Google Cloud.
  - Ejemplo local: `GOOGLE_APPLICATION_CREDENTIALS=/Users/abc/Desktop/CROW/chatbot-ai/admin-key.json`

**Instalación de Dependencias: Ejecuta pip install -r requirements.txt para instalar las dependencias necesarias.**

#### Ejecución Local:

- Inicia el servidor Flask con python app.py.
- Asegúrate de que los puertos y las URLs de callback estén configurados correctamente en tus servicios de WhatsApp y OpenAI.

#### Despliegue

- Para desplegar este bot, puedes utilizar servicios como Heroku, AWS, o Google Cloud. Asegúrate de configurar las variables de entorno en tu plataforma de despliegue.
- Una vez que el bot esté en funcionamiento, podrá interactuar con los usuarios a través de WhatsApp, responder preguntas y proporcionar información utilizando la inteligencia artificial de OpenAI.

### CONTRIBUIR

Si tienes ideas, preguntas o deseas discutir sobre las posibilidades de la IA y cómo trabajar juntos para construir soluciones basadas en IAG, no dudes en contactarme:

- GitHub: [https://github.com/albertgilopez](https://github.com/albertgilopez)
- LinkedIn: Albert Gil López: [https://www.linkedin.com/in/albertgilopez/](https://www.linkedin.com/in/albertgilopez/)
- Inteligencia Artificial Generativa (IAG) en español: [https://www.codigollm.es/](https://www.codigollm.es/)
