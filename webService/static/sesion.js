// Revalida la sesión cuando la pestaña vuelve al foco.
//
// El redirect al abrir la página (el before_request del backend) cubre al que
// entra o recarga. No cubre al que deja el panel abierto toda la jornada: la
// contraseña del CRM vive en memoria del proceso, así que un reinicio de noche
// la borra, y a la mañana el vendedor sigue en la pestaña de ayer. Como no
// navega, nadie chequea nada: pega el link, genera la tanda —gastando tokens— y
// se entera recién en el paso de órdenes.
//
// Va aparte de app.js porque lo necesitan también followers.html y admin.html,
// que no cargan app.js ni su interceptor de fetch.
(function revalidarAlVolverAlFoco() {
  const MINIMO_ENTRE_CHEQUEOS = 15000;   // no repreguntar si recién se preguntó
  let ultimoChequeo = 0;
  let yendoAlLogin = false;

  async function chequear() {
    if (yendoAlLogin || document.visibilityState !== "visible") return;
    const ahora = Date.now();
    if (ahora - ultimoChequeo < MINIMO_ENTRE_CHEQUEOS) return;
    ultimoChequeo = ahora;

    let r;
    try {
      r = await fetch("/api/sesion-viva", { cache: "no-store" });
    } catch (e) {
      return;   // sin red: no es asunto nuestro, que lo resuelva quien opere
    }
    if (r.status !== 401) return;

    let data = {};
    try { data = await r.clone().json(); } catch (e) { /* 401 sin JSON */ }
    // Solo con la marca `relogin`. Un 401 pelado es "no estás logueado", y en
    // las páginas públicas (las guías) eso es normal: no hay que redirigir.
    if (!data.relogin) return;

    yendoAlLogin = true;
    const volver = encodeURIComponent(location.pathname + location.search);
    location.href = `/login?motivo=sesion_crm&next=${volver}`;
  }

  document.addEventListener("visibilitychange", chequear);
  window.addEventListener("focus", chequear);
  window.addEventListener("pageshow", (e) => { if (e.persisted) chequear(); });
})();
