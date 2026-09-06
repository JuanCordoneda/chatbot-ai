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

- WHATSAPP_APP_SECRET: la clave secreta de la app (Meta → Configuración de la
  app → Básica). Con esto se verifica que el webhook lo mandó Meta y no
  cualquiera que haya descubierto la URL. **Setearla antes de exponer
  `/whatsapp` a internet**: procesar el webhook dispara envíos, así que sin
  firma la URL es un botón de "mandá una tanda" abierto al público. Si no está,
  el servicio avisa al arrancar y acepta igual (para no matar el webhook en
  instalaciones que todavía no la setearon).
- WHATSAPP_PEDIDO_COMENTARIOS: prende el pedido de comentarios por WhatsApp
  (mandarle al bot un mensaje con todos los comentarios pegados y recibirlos de
  a uno). **Apagado por defecto.** Se prende con `=1` y recreando el servicio.
  Apagado, el bot se comporta igual que antes: el mensaje va al modelo.
  Ojo: depende del webhook de Meta, que todavía no está configurado.

La sesión de Instagram del scraper se administra desde el panel
(**/admin → pestaña Instagram**): ahí se cargan una o más cuentas, se prueban
contra Instagram antes de guardarse y se ve cuál está en uso. Tener **dos**
cargadas es lo que evita el corte: cuando Instagram rechaza una, el scraper pasa
a la siguiente solo y avisa por WhatsApp. Variables relacionadas, todas
opcionales:

- INSTAGRAM_COOKIES_JSON: la sesión de toda la vida. Hoy es el **último**
  recurso: se usa solo si no hay ninguna cuenta cargada en el panel (o si la DB
  no está disponible). Se sigue pudiendo subir con `./script_COOKIE.sh`.
- IG_HEALTH_DESDE / IG_HEALTH_HASTA: ventana horaria del monitor, en hora
  argentina. Default 9 y 21.
- IG_HEALTH_CHEQUEOS: cuántos chequeos propios hace por día dentro de esa
  ventana. Default 15 (uno cada 48 min). Es un techo, no un piso: cada post que
  procesa un vendedor cuenta como prueba de vida y saltea el siguiente chequeo.
  **No conviene subirlo**: cada chequeo es una llamada autenticada real, y la
  versión anterior —una cada 5 minutos, las 24 h— es sospechosa de haber causado
  los checkpoints que venía a detectar.
- IG_HEALTH_SHORTCODE: el post público que se usa de sonda.
- IG_REINTENTO_MIN: cuánto espera antes de volver a probar una cuenta que
  Instagram rechazó. Default 30 min (un checkpoint se resuelve verificando la
  cuenta en el navegador y la misma cookie vuelve a servir).
- TELEGRAM_BOT_TOKEN + TELEGRAM_ALERTA_CHAT_ID: adónde salen los avisos.
  **Es el canal que hay que usar.** WhatsApp no sirve solo para esto: Meta
  contesta 200 y después descarta el mensaje si pasaron más de 24 h desde que
  el destinatario le escribió al bot (error 131047), y las caídas pasan los
  fines de semana, que es justo cuando esa ventana está cerrada. Durante un día
  entero ninguna alerta llegó y desde el server parecían enviadas. El chat_id se
  saca con `python scripts/telegram_chat_id.py` después de mandarle /start al bot.
- GROWI_ALERTA_WHATSAPP, WHATSAPP_API_URL, WHATSAPP_ACCESS_TOKEN: el segundo
  canal, que se manda igual (por si la ventana está abierta). **Todas tienen que
  estar en el servicio `openai`**, que es quien corre los monitores; si no hay
  ningún canal, los avisos quedan solo en el log (y el servicio lo dice).
- INTERNO_TOKEN: secreto compartido entre el webService y el openAIService para
  los endpoints de administración de sesiones. Si no está, se deriva de
  DATABASE_URL, que ambos ya comparten.

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
