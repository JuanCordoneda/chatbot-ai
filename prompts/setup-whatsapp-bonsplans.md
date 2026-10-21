Necesito que me configures un número de WhatsApp por la API oficial de Meta (WhatsApp Cloud API) para Crow Agency, usando el navegador. Trabajá en una pestaña nueva y explicame en una línea qué vas a hacer antes de cada paso.

# Qué es
Un número de promos llamado «Bons Plans Montmartre». Desde ahí un bot (ya está programado y subido a Railway) les manda ofertas de restaurantes del barrio a comercios de París 18e y contesta las respuestas. La primera oferta es de Toloache, un restaurante mexicano.

# Datos
- Nombre visible del número: Bons Plans Montmartre
- Categoría: Restaurante (si no está, «Otro»)
- Descripción: Les bons plans des restos de Montmartre et du 18e.
- Número de teléfono: te lo paso yo cuando lo pidas. Tiene que poder recibir un SMS o una llamada y no puede tener WhatsApp instalado en un celular.
- URL del webhook: https://whatsapp-service-production-2a69.up.railway.app/whatsapp
- Token de verificación del webhook: bonsplans-3285892490a1
- Imagen de la plantilla: /Users/abc/Desktop/CROW/toloache-prospector/plantilla-midi/toloache-midi-10.jpg

# Pasos
1. Entrá a business.facebook.com y buscá el portfolio comercial de Crow Agency. Si no existe o tengo varios, pará y preguntame cuál usar.
2. En developers.facebook.com creá una app de tipo «Empresa» (Business) llamada «Bons Plans Montmartre Bot», vinculada a ese portfolio, y agregale el producto WhatsApp.
3. En WhatsApp › Configuración de la API, agregá el número con los datos de arriba. Cuando llegue el código de verificación por SMS o llamada, pedímelo. Si Meta pide un PIN de verificación en dos pasos, pedime que lo elija yo y anotalo para el resumen final.
4. Anotá el ID del número de teléfono (Phone number ID) y el ID de la cuenta de WhatsApp Business (WABA ID).
5. En el portfolio, en Configuración del negocio › Usuarios › Usuarios del sistema, creá un usuario del sistema llamado «bot-bonsplans» con rol de administrador. Asignale la app y la cuenta de WhatsApp con control total. Generá un token para esa app, que no venza nunca, con los permisos whatsapp_business_messaging y whatsapp_business_management.
6. En la app, WhatsApp › Configuración › Webhook: poné la URL y el token de verificación de arriba, tocá «Verificar y guardar» y suscribite al campo «messages».
7. En WhatsApp Manager › Configuración de pagos hay que cargar una tarjeta para poder mandar plantillas. No cargues ningún dato de pago: avisame y lo hago yo.
8. En WhatsApp Manager › Plantillas de mensajes, creá esta plantilla y mandala a revisión:
   - Categoría: Marketing. Nombre: bonsplans_toloache_midi. Idioma: francés.
   - Cabecera: imagen (subí el archivo de arriba).
   - Cuerpo: Bonjour {{1}} ! Ici Bons Plans Montmartre, les bonnes adresses du quartier. En ce moment chez Toloache, le resto mexicain du 49 rue de Clignancourt (M° Château Rouge) : -10 % sur la carte le midi, du mardi au vendredi de 12h à 15h, jusqu'au 18 décembre. Je vous envoie l'offre pour vous et vos collègues ?
   - Ejemplo para {{1}}: Claire
   - Pie: Répondez STOP pour ne plus recevoir nos messages
   - Botones de respuesta rápida: «Oui, avec plaisir» y «Non merci»

# Reglas
- Pará y preguntame antes de: aceptar términos o condiciones, crear un portfolio comercial nuevo, subir documentos de verificación del negocio, o cualquier cosa que cueste plata.
- Los códigos de SMS, contraseñas y datos de pago los pongo yo. No los adivines ni los busques.
- No mandes mensajes de WhatsApp a nadie, ni de prueba.
- No borres ni cambies otras apps, números o cuentas que ya existan.
- Si Meta rechaza el nombre «Bons Plans Montmartre» o pide una web, pará y avisame.
- Si algo falla dos veces seguidas, pará y contame qué viste.

# Al terminar
Pasame un resumen con:
- Phone number ID
- WABA ID
- El token permanente (solo en este chat, no lo pegues en ningún otro lado)
- El PIN de dos pasos, si se creó
- Si el webhook quedó verificado y suscripto a «messages»
- El estado de la plantilla (en revisión, aprobada o rechazada, con el motivo)
- Cualquier paso que haya quedado pendiente
