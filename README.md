# Prospector de transportistas (Ignacio)

En esta rama el bot hace prospección: le escribe por WhatsApp a una lista de transportistas, les hace una entrevista corta y deja todo en un panel web.

**Cómo se usa**

1. Entrá al panel: `https://<openai-service>/panel` (usuario `ignacio`, contraseña `PANEL_PASSWORD`).
2. **Cargar lista**: pegá un contacto por línea (`Nombre, Empresa, Teléfono`) o subí un CSV.
3. **Escribirles a los nuevos**: el bot manda la plantilla de WhatsApp a cada uno, de a uno.
4. Cuando contestan, el bot sigue solo: pregunta si tiene camiones o colectivos, cuántas unidades, qué marcas y quién compra los filtros. En el panel ves cada charla y hasta qué paso llegó.
5. Si un cliente pregunta precios o pide hablar con una persona, el bot pausa y lo marca como "Te necesita". Desde el panel podés escribirle vos (el bot queda pausado) o volver a activar el bot.

**Antes de arrancar**

- Crear y aprobar en Meta (WhatsApp Manager › Plantillas) una plantilla de marketing llamada `prospeccion_filtros`, idioma español (Argentina), con este cuerpo:
  `Hola {{1}}, ¿cómo va? Soy del equipo de Ignacio, vendemos filtros para camiones y colectivos. ¿Te puedo hacer unas preguntas rápidas sobre tu flota?`
  Sin plantilla aprobada, WhatsApp no deja escribirle primero a nadie.
- Variables nuevas del `openai-service`:
  - `PANEL_PASSWORD`: contraseña del panel (obligatoria en producción).
  - `WHATSAPP_SERVICE_URL`: URL del `whatsapp-service` (por ejemplo `https://<whatsapp-service>`).
  - `DB_PATH`: archivo de la base (por defecto `/data/prospector.db`). En Railway montá un volumen en `/data`, si no la base se borra en cada deploy.
  - Opcionales: `PROSPECT_TEMPLATE`, `PROSPECT_TEMPLATE_LANG`, `PROSPECT_TEMPLATE_TEXT` (si cambiás la plantilla), `ANTHROPIC_MODEL`, `PAUSA_ENTRE_ENVIOS` (segundos entre envíos, por defecto 2).
- Qué ofrece Ignacio, la zona y desde cuántas unidades un cliente está calificado se cambian desde **Ajustes** en el panel.

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
