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

- WHATSAPP_PEDIDO_COMENTARIOS: prende el pedido de comentarios por WhatsApp
  (mandarle al bot un mensaje con todos los comentarios pegados y recibirlos de
  a uno). **Apagado por defecto.** Se prende con `=1` y recreando el servicio.
  Apagado, el bot se comporta igual que antes: el mensaje va al modelo.
  Ojo: depende del webhook de Meta, que todavía no está configurado.

Modelos de IA (opcionales — si no se setean, valen los defaults):

- CROW_MODEL_PRO: modelo de los clientes marcados **Pro** en el admin. Default `claude-opus-4-8`.
- CROW_MODEL_STANDARD: modelo de los clientes **Estándar**. Default `claude-sonnet-5`.
- CROW_MODEL_VISION: modelo que describe la imagen del post. Default: el estándar.
  Es una llamada corta por post y no mejora con el modelo caro, por eso va aparte
  de la calidad del cliente.

La calidad (`pro` / `standard`) se carga por cliente desde el admin. Subir de
familia de modelo es cambiar estas variables y reiniciar el servicio: no hace
falta tocar código. Antes de bajar clientes a estándar, comparar las dos tandas
con `python scripts/comparar_calidad.py` (el prompt está calibrado contra el pro).

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
