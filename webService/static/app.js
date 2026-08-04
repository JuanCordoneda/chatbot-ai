let currentUrl = "";
// Modo keyword: la tanda es N veces una palabra ("CLAUDE" / "Claude" / "claude").
// Se guarda del post en curso para que "Cargar más" y el reintento sigan en el
// mismo modo en vez de caer a la generación normal.
let currentKeyword = "";
let comentariosGenerados = [];
let generosGenerados = [];        // género por índice: "hombres" | "mujeres" | null | "__header__"
let generoActual = null;          // género de la sección que se está streameando
let esMixto = false;              // cliente mixto → 2 columnas desde el arranque
let generoFijo = null;            // cliente male/female → "hombres"/"mujeres": UNA sola sección
let ultimoEsVideo = false;        // último post: ¿es video? (para el bloque de transcripción)
let scrapeRecibido = false;       // ¿ya llegó el evento de scrape? (define si ultimoEsVideo es confiable)
let tiposGenerados = [];          // "verificado" (default) | "noverif" por índice
// Reparto automático V/NV al generar. Los objetivos salen de la ficha del
// cliente (rangos de comentarios): "generame 40 verificados y 40 comunes". Los
// que entran dentro del objetivo se marcan solos; el resto queda de reserva.
let objetivoV = 0, objetivoNV = 0;
let asignadosV = 0, asignadosNV = 0;
// Mientras corre "+ generar más" de una lista, TODO lo que llega va a esa lista.
let tipoForzado = null;
let currentJobId = null;
let streamOffset = 0;
let streamProgresoOffset = 0;
let streamResets = 0;   // cuántos "reset" ya aplicó el front (para no re-aplicarlos al reconectar)
let streamMeta = {};
// Corte manual de la generación (al publicar): aborta el fetch del stream y
// frena el reintento automático de reconexión.
let streamAbort = null;
let streamCancelado = false;
let esperandoTranscripcion = false;
let pendingComentarios = [];

// Los encabezados de género (hombres:/mujeres:) que emite la IA no son comentarios:
// le dicen a Growi de qué género es cada bloque. El backend los usa; acá los
// detectamos para no mostrarlos como comentarios y reinyectarlos al enviar.
const _HEADERS_GENERO = {
  mujeres: "mujeres", mujer: "mujeres", women: "mujeres", female: "mujeres",
  hombres: "hombres", hombre: "hombres", men: "hombres", male: "hombres",
};
// Detecta la línea marcadora de sección de género. Tolerante a variantes del
// modelo (mayúsculas, markdown "**mujeres:**", espacios "mujeres :", inglés
// "women:") para que un header mal escrito no rompa la asignación de género
// (bug: si no se reconoce "mujeres:", todos los comentarios caían en "hombres").
// Requiere que la línea COMPLETA sea el marcador (palabra + ":"), no un comentario
// que casualmente arranque con esa palabra.
function generoDeHeader(t) {
  const s = (t || "").trim().toLowerCase()
    .replace(/^[*_#>`~\s-]+/, "")
    .replace(/[*_`~\s]+$/, "");
  const m = s.match(/^([a-zñáéíóú]+)\s*:$/);
  return m ? (_HEADERS_GENERO[m[1]] || null) : null;
}

// Texto normalizado para detectar comentarios duplicados (minúsculas, sin
// espacios de más). Se usa al "Cargar más" para no repetir lo ya mostrado.
function normComentario(t) {
  return (t || "").trim().toLowerCase().replace(/\s+/g, " ");
}

// Tokens de solo-letras/números (sin emojis ni puntuación), para comparar
// comentarios "casi iguales" (paráfrasis, abreviaturas, emoji de más, etc.).
function tokensComentario(t) {
  return normComentario(t).replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
}

function infoComentario(t) {
  const tokens = tokensComentario(t);
  return { norm: normComentario(t), tokens, base: tokens.join(" ") };
}

// ¿`cand` es un casi-duplicado de alguno de `existentes`? Atrapa: texto idéntico,
// uno contenido en el otro ignorando emojis/puntuación ("God is good" vs
// "God is good 🙏🏾", "keep building bro" vs "keep building brother"), y alto
// solapamiento de palabras ("real leadership starts with action" vs "leadership
// starts with action not promises").
function esCasiDuplicado(cand, existentes) {
  for (const ex of existentes) {
    if (cand.norm === ex.norm) return true;
    // Contención: uno incluido en el otro. Solo cuenta si el más corto tiene ≥3
    // palabras, para no tragarnos comentarios cortos legítimos ("fire" dentro de
    // "this is fire") de los que la tanda tiene muchos a propósito.
    if (cand.base && ex.base && Math.min(cand.tokens.length, ex.tokens.length) >= 3 &&
        (cand.base.includes(ex.base) || ex.base.includes(cand.base))) return true;
    if (cand.tokens.length && ex.tokens.length) {
      const setEx = new Set(ex.tokens);
      let inter = 0;
      for (const tk of cand.tokens) if (setEx.has(tk)) inter++;
      const union = new Set([...cand.tokens, ...ex.tokens]).size;
      // Muchas palabras en común (≥4) o solapamiento alto (Jaccard ≥ 0.6).
      if (inter >= 4 || inter / union >= 0.6) return true;
    }
  }
  return false;
}

// Al refrescar, el navegador restaura el scroll donde estaba (a veces a mitad
// del body). Lo desactivamos y arrancamos siempre arriba.
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
window.scrollTo(0, 0);
window.addEventListener("load", () => window.scrollTo(0, 0));
window.addEventListener("pageshow", () => window.scrollTo(0, 0));

function show(id) {
  document.getElementById(id).classList.remove("hidden");
  // Al cambiar de step (no al abrir overlays/modales) subimos al top.
  if (id.startsWith("step-")) window.scrollTo(0, 0);
  // El reloj se engancha acá y no en cada llamador: al overlay lo abre y lo
  // cierra media docena de lugares distintos.
  if (id === "loading-overlay") arrancarRelojOverlay();
}

function hide(id) {
  document.getElementById(id).classList.add("hidden");
  if (id === "loading-overlay") pararRelojOverlay();
}

function setError(msg) {
  const el = document.getElementById("error-msg");
  el.textContent = msg;
  el.classList.toggle("hidden", !msg);
}

function setProgreso(msg) {
  const overlay = document.getElementById("loading-text");
  if (overlay) overlay.textContent = msg;
  const stepCard = document.getElementById("step-card");
  const status = document.getElementById("stream-status");
  if (status && stepCard && !stepCard.classList.contains("hidden")) return;
  if (status) status.textContent = msg;
}

// Guard de re-entrada: sin esto, un doble click arranca DOS generaciones (dos
// llamadas pagas a la API) y las dos escriben en la misma lista, dejando el doble
// de comentarios mezclados. Se libera al terminar el stream o ante un error.
let generando = false;

async function generarComentarios() {
  if (generando) return;

  const url = document.getElementById("ig-link").value.trim();
  if (!url) {
    setError("Pegá un link de Instagram primero.");
    return;
  }
  // Cliente de palabra clave: sin palabra no se genera (le saldrían comentarios
  // normales, que no es lo que ese cliente compra).
  const keywordOn = esPostKeyword();
  const keyword = keywordOn ? document.getElementById("ig-keyword").value.trim() : "";
  if (keywordOn && !keyword) {
    setError("Este cliente usa comentarios de palabra clave: escribí la palabra.");
    document.getElementById("ig-keyword").focus();
    return;
  }

  generando = true;
  setError("");
  ocultarIaError();
  guardarLinkReciente(url);
  currentUrl = url;
  currentKeyword = keyword;
  currentJobId = null;
  streamCancelado = false;   // post nuevo: vuelve a habilitarse el stream
  streamOffset = 0;
  streamProgresoOffset = 0;
  streamResets = 0;
  vistosStream = new Set();
  ultimoItemTocado = null;
  streamMeta = {};
  esMixto = false;
  generoFijo = null;
  ultimoEsVideo = false;
  scrapeRecibido = false;
  comentariosGenerados = [];
  generosGenerados = [];
  generoActual = null;
  tiposGenerados = [];
  esperandoTranscripcion = false;
  pendingComentarios = [];

  hide("step-input");
  hide("step-comentarios");
  hide("step-resultado");
  show("loading-overlay");
  setProgreso("Iniciando...");

  try {
    const resp = await fetch("/api/procesar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, keyword }),
    });
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    currentJobId = data.job_id;
  } catch (e) {
    generando = false;
    hide("loading-overlay");
    show("step-input");
    setError(e.message);
    return;
  }

  // Mostrar la sección de comentarios vacía y conectar el stream
  document.getElementById("lista-comentarios").innerHTML = "";
  // Regenerar skeleton
  const oldSkeleton = document.getElementById("skeleton-list");
  if (!oldSkeleton) {
    const sk = document.createElement("div");
    sk.id = "skeleton-list";
    sk.className = "skeleton-list";
    sk.innerHTML = SKELETON_HTML;
    document.getElementById("lista-comentarios").before(sk);
  }
  document.getElementById("client-badge").textContent = "";
  document.getElementById("transcription-block").classList.add("hidden");
  const pdBlock = document.getElementById("photo-description-block");
  if (pdBlock) pdBlock.classList.add("hidden");
  document.getElementById("scrape-caption-block").classList.add("hidden");
  document.getElementById("status-listo").classList.add("hidden");
  document.getElementById("comments-actions-bar").classList.add("hidden");
  document.getElementById("stream-status").textContent = "";
  hide("loading-overlay");
  show("step-comentarios");
  setProgreso("Accediendo al post...");

  conectarStream();
}

function conectarStream() {
  if (!currentJobId || streamCancelado) return;

  streamAbort = new AbortController();
  const url = `/api/stream/${currentJobId}?offset=${streamOffset}&progreso_offset=${streamProgresoOffset}&resets=${streamResets}`;
  const reader = fetch(url, { signal: streamAbort.signal }).then((r) => r.body.getReader());

  reader.then(async (r) => {
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await r.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let evento;
          try {
            evento = JSON.parse(line.slice(6));
          } catch (parseErr) {
            console.error("[stream] parse error:", parseErr, "line:", line);
            continue;
          }
          try { manejarEvento(evento); } catch (uiErr) { console.error("[stream] ui error:", uiErr); }
        }
      }
    } catch (e) {
      if (streamCancelado) return;   // lo cortamos nosotros: no reconectar
      console.error("[stream] catch error:", e);
      setProgreso("Reconectando...");
      setTimeout(conectarStream, 1500);
    }
  }).catch((e) => {
    if (streamCancelado) return;
    console.error("[stream] fetch catch:", e);
    setProgreso("Reconectando...");
    setTimeout(conectarStream, 1500);
  });
}

// Corta la generación en curso: se llama al apretar "Publicar seleccionados".
// Aborta el stream (para que el navegador no siga leyendo ni reconecte) y avisa
// al backend, que deja de consumir la IA. Es idempotente.
function cancelarGeneracion() {
  if (streamCancelado) return;
  streamCancelado = true;
  generando = false;
  try { streamAbort && streamAbort.abort(); } catch { /* ya cerrado */ }
  streamAbort = null;
  const jobId = currentJobId;
  if (jobId) {
    fetch(`/api/cancelar/${jobId}`, { method: "POST", keepalive: true })
      .catch(() => { /* si falla, el job igual muere solo por TTL */ });
  }
  setProgreso("");
  const barra = document.getElementById("stream-status");
  if (barra) barra.textContent = "";
  ocultarChunk();
}

function manejarEvento(evento) {
  if (evento.tipo === "progreso") {
    setProgreso(evento.mensaje);
    streamProgresoOffset++;
  } else if (evento.tipo === "scrape") {
    mostrarScrape(evento);
  } else if (evento.tipo === "step") {
    // El backend manda el step de transcripción también en posts de foto, donde
    // no hay video que transcribir: mostrar "Generando la transcripción del
    // video..." ahí es mentira. Como el evento de scrape llega antes, ya sabemos
    // si es video y podemos ignorarlo.
    if (evento.nombre === "transcription" && scrapeRecibido && !ultimoEsVideo) {
      mostrarStep("imagen");
      return;
    }
    if (evento.nombre === "transcription") {
      esperandoTranscripcion = true;
      const skeleton = document.getElementById("skeleton-list");
      if (skeleton) skeleton.style.visibility = "hidden";
    }
    mostrarStep(evento.nombre);
  } else if (evento.tipo === "transcripcion") {
    esperandoTranscripcion = false;
    const skeleton = document.getElementById("skeleton-list");
    if (skeleton) skeleton.style.visibility = "";
    mostrarEstadoTranscripcion(evento.texto);
    pendingComentarios.forEach((e) => {
      ocultarChunk();
      agregarComentarioFiltrado(e.texto, e.index);
    });
    pendingComentarios = [];
  } else if (evento.tipo === "chunk") {
    if (!esperandoTranscripcion) mostrarChunk(evento.texto);
  } else if (evento.tipo === "comentario") {
    streamOffset = evento.index + 1;
    if (esperandoTranscripcion) {
      pendingComentarios.push(evento);
    } else {
      ocultarChunk();
      agregarComentarioFiltrado(evento.texto, evento.index);
    }
  } else if (evento.tipo === "reset") {
    // La generación salió cortada y el backend reintenta desde cero:
    // descartamos todo lo mostrado hasta acá. Contamos el reset para no
    // volver a aplicarlo si el stream se reconecta (si no, borraría los
    // comentarios ya mostrados de una generación completa).
    streamResets++;
    vistosStream = new Set();
    document.getElementById("lista-comentarios").innerHTML = "";
    comentariosGenerados = [];
    generosGenerados = [];
    generoActual = generoFijo;
    tiposGenerados = [];
    asignadosV = 0;
    asignadosNV = 0;
    pendingComentarios = [];
    streamOffset = 0;
    // El reset borró el DOM: si es mixto, re-armamos las 2 columnas vacías.
    if (esMixto) { _prepararPaneles(); }
    actualizarConteo();
  } else if (evento.tipo === "cancelado") {
    // El backend confirma que cortó: no reconectamos ni mostramos nada más.
    streamCancelado = true;
    generando = false;
  } else if (evento.tipo === "listo") {
    streamMeta = evento;
    finalizarStream(evento);
  } else if (evento.tipo === "error") {
    generando = false;
    hide("loading-overlay");
    // Si el post ya se scrapeó (parcial), NO tiramos abajo la pantalla: dejamos
    // la transcripción/descripción que sí salieron y avisamos en el lugar, con
    // opción a reintentar. Solo volvemos al inicio si no hay nada que mostrar.
    if (evento.parcial) {
      const sk = document.getElementById("skeleton-list");
      if (sk) sk.remove();
      document.getElementById("stream-status").textContent = "";
      mostrarIaError(evento.mensaje, evento.reintentable !== false);
    } else {
      hide("step-comentarios");
      show("step-input");
      setError(evento.mensaje);
    }
  }
}

// Cartel de error de IA dentro de la pantalla de comentarios (post ya scrapeado).
function mostrarIaError(msg, reintentable) {
  const banner = document.getElementById("ia-error-banner");
  if (!banner) return;
  document.getElementById("ia-error-msg").textContent =
    msg || "No se pudieron generar los comentarios. Reintentá en un momento.";
  document.getElementById("ia-error-retry").classList.toggle("hidden", !reintentable);
  banner.classList.remove("hidden");
}

// Muestra/oculta el bloque de la palabra clave. NO lo decide el vendedor: se
// prende solo cuando el post es de un cliente marcado como "palabra clave" en su
// ficha (eso lo dice el backend).
function mostrarKeyword(on) {
  document.getElementById("ig-keyword-wrap").classList.toggle("hidden", !on);
  if (!on) document.getElementById("ig-keyword").value = "";
}

// ¿El post en pantalla es de un cliente de palabra clave?
function esPostKeyword() {
  return !document.getElementById("ig-keyword-wrap").classList.contains("hidden");
}

// ── Sugerencia de palabra clave ──────────────────────────────────────────────
// El caption de estos posts casi siempre dice cuál es ("comment CLAUDE to get
// the PDF"), así que apenas se pega el link la buscamos y PRECARGAMOS el campo.
// Es una sugerencia, no una decisión: queda editable y el vendedor la ve antes
// de generar. Si la detección se equivoca, la corrige; si no hay nada claro, el
// backend devuelve vacío y no tocamos nada.
let keywordSugeridaPara = "";   // link para el que ya pedimos sugerencia
let keywordSugerida = "";       // la última sugerida (para no pisar lo que escribió el vendedor)

async function sugerirKeyword() {
  const url = document.getElementById("ig-link").value.trim();
  if (!url || url === keywordSugeridaPara) return;
  keywordSugeridaPara = url;

  let data;
  try {
    const resp = await fetch("/api/sugerir-keyword?url=" + encodeURIComponent(url));
    data = await resp.json();
  } catch (e) {
    return;   // silencioso: la sugerencia es un extra, no un paso del flujo
  }
  // Se pegó otro link mientras viajaba la respuesta: ya no aplica.
  if (document.getElementById("ig-link").value.trim() !== url) return;

  // El cliente no trabaja con palabra clave: el bloque ni aparece.
  if (!data || !data.keyword_mode) {
    mostrarKeyword(false);
    keywordSugerida = "";
    return;
  }
  mostrarKeyword(true);

  const kw = (data.keyword || "").trim();
  const campo = document.getElementById("ig-keyword");
  const hint = document.getElementById("ig-keyword-hint");
  if (!kw) {
    // Cliente de palabra clave pero el caption no la dice (o la dice de una
    // forma que no reconocemos): la escribe el vendedor.
    if (hint) {
      hint.textContent = "No encontramos la palabra en el post: escribila vos.";
      hint.classList.add("ig-keyword-hint--detectada");
    }
    return;
  }
  // Solo pisamos lo que pusimos nosotros: si el vendedor ya escribió su palabra,
  // la suya manda.
  const escrito = campo.value.trim();
  if (escrito && escrito !== keywordSugerida) return;

  campo.value = kw;
  keywordSugerida = kw;
  if (hint) {
    hint.textContent = "Detectada en el post: revisala antes de generar.";
    hint.classList.add("ig-keyword-hint--detectada");
  }
}

function ocultarIaError() {
  const banner = document.getElementById("ia-error-banner");
  if (banner) banner.classList.add("hidden");
}

// Reintenta SOLO la generación, reusando el post ya scrapeado (el backend cachea
// el scrape/transcripción por shortcode, así que no re-baja ni re-transcribe).
function reintentarGeneracion() {
  if (!currentUrl || generando) return;
  ocultarIaError();
  document.getElementById("ig-link").value = currentUrl;
  // El reintento tiene que salir en el mismo modo que la generación original.
  mostrarKeyword(!!currentKeyword);
  document.getElementById("ig-keyword").value = currentKeyword;
  generarComentarios();
}

// ── Bloques de contexto (pie, descripción, transcripción) ─────────────────

// Copiar sin togglear el <details>: el botón vive dentro del <summary>.
async function copiarContexto(ev, idTexto) {
  ev.preventDefault();
  ev.stopPropagation();
  const el = document.getElementById(idTexto);
  const txt = el ? el.textContent.trim() : "";
  if (!txt) return;
  try {
    await navigator.clipboard.writeText(txt);
  } catch (e) {
    return;   // sin permiso de portapapeles: no avisamos con un error, es un extra
  }
  const btn = ev.currentTarget;
  const antes = btn.textContent;
  btn.textContent = "✓ Copiado";
  btn.classList.add("sd-copy--ok");
  setTimeout(() => {
    btn.textContent = antes;
    btn.classList.remove("sd-copy--ok");
  }, 1400);
}

// Largo del texto en el encabezado: dice de un vistazo si la IA leyó dos líneas
// o tres párrafos, sin tener que abrir el bloque.
function marcarLargoContexto(idTexto, idMeta) {
  const meta = document.getElementById(idMeta);
  const el = document.getElementById(idTexto);
  if (!meta || !el) return;
  const txt = el.textContent.trim();
  const palabras = txt ? txt.split(/\s+/).length : 0;
  meta.textContent = palabras ? `${palabras} palabras` : "";
  // Texto largo: el bloque hace scroll interno, así que lo avisamos.
  meta.classList.toggle("sd-meta--largo", palabras > 120);
}

function refrescarMetaContexto() {
  marcarLargoContexto("scrape-caption-text", "scrape-caption-meta");
  marcarLargoContexto("photo-description-text", "photo-description-meta");
  marcarLargoContexto("transcription-text", "transcription-meta");
}

// ── Reloj del overlay ─────────────────────────────────────────────────────
// El proceso no reporta porcentaje, así que lo único honesto que podemos dar es
// cuánto lleva. Sirve para saber si se colgó o simplemente es un video largo.
let overlayTimer = null;

function arrancarRelojOverlay() {
  const el = document.getElementById("loading-elapsed");
  if (!el) return;
  const desde = Date.now();
  el.textContent = "0s";
  clearInterval(overlayTimer);
  overlayTimer = setInterval(() => {
    const s = Math.floor((Date.now() - desde) / 1000);
    el.textContent = s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
  }, 1000);
}

function pararRelojOverlay() {
  clearInterval(overlayTimer);
  overlayTimer = null;
}

// El esqueleto imita la fila real (número + check + texto + switches): así el
// salto al llegar el primer comentario es mínimo, en vez de barras sueltas que
// no se parecen a nada de lo que viene después.
const SKELETON_HTML = [88, 72, 94, 61, 80]
  .map(
    (ancho) => `
    <div class="skeleton-row">
      <span class="skeleton-px skeleton-px--num"></span>
      <span class="skeleton-px skeleton-px--chk"></span>
      <span class="skeleton-px skeleton-px--txt" style="width:${ancho}%"></span>
      <span class="skeleton-px skeleton-px--sw"></span>
    </div>`
  )
  .join("");

const STEP_LABELS = {
  transcription: { icon: "🎙️", texto: "Generando la transcripción del video...", sub: "menos de 60 segundos" },
  // Misma tarjeta para los posts de foto, donde no hay video que transcribir:
  // antes ese momento quedaba sin ningún aviso.
  imagen: { icon: "🖼️", texto: "Analizando la imagen del post...", sub: "unos segundos" },
  procesando: { icon: "⏳", texto: "Analizando el post...", sub: "un momento" },
};

function mostrarStep(nombre) {
  const card = document.getElementById("step-card");
  if (!nombre) {
    card.classList.add("hidden");
    return;
  }
  document.getElementById("stream-status").textContent = "";
  const info = STEP_LABELS[nombre] || { icon: "⏳", texto: nombre, sub: "" };
  card.innerHTML = `
    <div class="step-card-icon">${info.icon}</div>
    <div class="step-card-body">
      <span class="step-card-title">${info.texto}</span>
      ${info.sub ? `<span class="step-card-sub">${info.sub}</span>` : ""}
    </div>
    <div class="step-card-spinner"></div>
  `;
  card.classList.remove("hidden");
}

// La caja amarilla de "tipeando" quedó descartada: aparecía y desaparecía con
// cada comentario y era puro ruido visual. Del chunk solo aprovechamos la señal
// de que ya empezó a llegar texto, para sacar el skeleton.
function mostrarChunk(_texto) {
  const skeleton = document.getElementById("skeleton-list");
  if (skeleton) skeleton.remove();
}

function ocultarChunk() {}
function finalizarChunk() {}

// Orden de los bloques de contexto arriba de los comentarios:
//   foto  → Pie de página, Descripción de imagen
//   video → Pie de página, Transcripción, Descripción de la portada
// La fila es flex-column, así que alcanza con el `order` de cada bloque.
function _ordenarBloquesContexto(esVideo) {
  const cap = document.getElementById("scrape-caption-block");
  const desc = document.getElementById("photo-description-block");
  const tr = document.getElementById("transcription-block");
  if (!cap || !desc || !tr) return;
  cap.style.order = "1";
  tr.style.order = esVideo ? "2" : "3";
  desc.style.order = esVideo ? "3" : "2";
}

function mostrarScrape(data) {
  document.getElementById("scrape-owner").textContent = data.owner_username || "—";
  // Si el post no es de un cliente cargado no mostramos el @cuenta acá: se veía
  // igual que un cliente y hacía creer que el envío ya tenía a quién cobrarle.
  const badge = document.getElementById("client-badge");
  badge.textContent = data.cliente_asignado
    ? (data.client_id || data.owner_username || "—")
    : "Sin cliente asignado";
  badge.classList.toggle("scrape-value--accent", !!data.cliente_asignado);
  badge.classList.toggle("scrape-value--none", !data.cliente_asignado);
  ultimoEsVideo = !!data.is_video;
  scrapeRecibido = true;
  _ordenarBloquesContexto(ultimoEsVideo);

  // Rangos de cantidades del cliente (TAREA 6): el modal de órdenes autocompleta
  // cada tipo con rango (likes/views/shares/reposts/saves/reach) con un valor random.
  if (data.ranges !== undefined) window._clientRanges = data.ranges || {};
  // Cantidad de comentarios de la ficha del cliente: autocompleta los dos
  // casilleros del reparto con un número al azar dentro del rango configurado.
  _autocompletarComentarios(data.cliente_asignado ? data.client_id : "");
  // @usuario del cliente: se usa para consultar qué cantidades ya se le enviaron
  // y, sobre todo, para saber de qué campaña sale la plata al enviar tráfico.
  // Se pisa SIEMPRE, incluso vacío: antes, si un scrape no resolvía el dueño,
  // quedaba el del post anterior y el tráfico se le cobraba a ese otro cliente.
  window._clientIg = data.owner_username || "";
  // ¿El post es de un cliente cargado? Si no, no hay de dónde deducir la
  // campaña y se la pedimos a mano en el paso de órdenes.
  window._clienteAsignado = !!data.cliente_asignado;
  window._ventaElegida = "";

  hide("loading-overlay");

  if (data.caption) {
    document.getElementById("scrape-caption-text").textContent = data.caption;
    document.getElementById("scrape-caption-block").classList.remove("hidden");
  }
  const pdBlock2 = document.getElementById("photo-description-block");
  const pdText = document.getElementById("photo-description-text");
  const pdSum = document.getElementById("photo-description-summary");
  if (data.photo_description) {
    if (pdSum) pdSum.textContent = data.is_video ? "Descripción del video" : "Descripción de imagen";
    pdText.textContent = data.photo_description;
    pdText.classList.remove("desc-error");
    pdBlock2.classList.remove("hidden");
  } else if (data.descripcion_error) {
    // La descripción no salió por una falla de la IA (saturada / sin crédito):
    // lo decimos, en vez de dejar el bloque vacío o directamente no mostrarlo.
    if (pdSum) pdSum.textContent = data.is_video ? "Descripción del video" : "Descripción de imagen";
    pdText.textContent = "⚠️ " + data.descripcion_error;
    pdText.classList.add("desc-error");
    pdBlock2.classList.remove("hidden");
  }

  refrescarMetaContexto();

  // Cliente mixto (sin género fijo): armamos las 2 columnas (Hombres | Mujeres)
  // vacías desde el arranque, para que la de Hombres no aparezca recién al final
  // cuando termina la de Mujeres. Los comentarios luego llenan cada columna.
  // En modo keyword no hay hombres/mujeres que separar: son todos la misma
  // palabra. Va una sola sección, sin las 2 columnas.
  if (currentKeyword) {
    esMixto = false;
    generoFijo = null;
    generoActual = null;
    _prepararPaneles();
    return;
  }
  const g = (data.gender || "").toString().toLowerCase();
  esMixto = !(g === "male" || g === "female");
  // Cliente de un solo género: fijamos la sección. Aunque la IA se mande un
  // header del otro género, todos los comentarios van a la sección del cliente
  // y la otra columna nunca aparece.
  generoFijo = g === "male" ? "hombres" : (g === "female" ? "mujeres" : null);
  generoActual = generoFijo;
  _prepararPaneles();
}

// Arma los paneles (y, si el cliente es mixto, las 2 columnas de género) vacíos
// antes de que llegue el primer comentario: así la pantalla no salta.
function _prepararPaneles() {
  if (objetivoV || !objetivoNV) _panelTipo("verificado");
  if (objetivoNV) _panelTipo("noverif");
  if (esMixto) {
    for (const t of ["verificado", "noverif"]) {
      if (!document.querySelector(`.tipo-panel[data-tipo="${t}"]`)) continue;
      _seccionItems("hombres", t);
      _seccionItems("mujeres", t);
    }
  }
  _refrescarSecciones();
}

function mostrarEstadoTranscripcion(texto) {
  const tieneTranscripcion = texto && !texto.startsWith("(");
  // Foto: no mostramos el bloque de "Transcripción del video" (ya se muestra la
  // descripción de imagen en su propio bloque).
  if (!tieneTranscripcion && !ultimoEsVideo) {
    document.getElementById("transcription-block").classList.add("hidden");
    return;
  }
  // Si falló, el backend manda el motivo entre paréntesis: lo mostramos tal cual
  // (rate limit de Instagram, sesión vencida, video que no se pudo bajar…) en vez
  // del genérico "sin transcripción", que no le decía nada a nadie.
  document.getElementById("transcription-text").textContent = tieneTranscripcion
    ? texto
    : (texto ? texto.replace(/^\(|\)$/g, "") : "Sin transcripción disponible para este post.");
  document.getElementById("transcription-block").classList.remove("hidden");
  refrescarMetaContexto();
}

// ── Secciones por género en la lista de comentarios ──────────────────────────
// Los comentarios se muestran AGRUPADOS (todos los de hombres juntos, todos los
// de mujeres juntos) para que el vendedor solo elija Veri/No-Veri, en vez de una
// lista plana mezclada. Las secciones acumulan: "Cargar más" suma a la sección
// existente en lugar de abrir una nueva.
const _SECCIONES = [
  { key: "hombres", label: "Hombres",         icon: "♂" },
  { key: "mujeres", label: "Mujeres",         icon: "♀" },
  { key: "otros",   label: "Sin especificar", icon: "•" },
];

// Panel de una de las dos listas (verificados / comunes). Cada una acumula sus
// propias secciones de género y tiene su "+ generar más".
const _TIPOS_PANEL = [
  { key: "verificado", label: "Verificados", icon: "✅", corto: "V" },
  { key: "noverif",    label: "Comunes",     icon: "💬", corto: "NV" },
];

function _panelTipo(tipo) {
  const key = tipo === "noverif" ? "noverif" : "verificado";
  const lista = document.getElementById("lista-comentarios");
  let panel = lista.querySelector(`.tipo-panel[data-tipo="${key}"]`);
  if (!panel) {
    const meta = _TIPOS_PANEL.find(t => t.key === key);
    panel = document.createElement("div");
    panel.className = `tipo-panel tipo-panel--${key}`;
    panel.dataset.tipo = key;
    panel.innerHTML = `
      <div class="tipo-panel-header">
        <span class="tp-icon">${meta.icon}</span>
        <span class="tp-label">${meta.label}</span>
        <span class="tp-count">0</span>
        <span class="tp-sel hidden">0 elegidos</span>
        <button type="button" class="tp-all" onclick="togglePanel('${key}')">Todos</button>
        <button type="button" class="tp-mas" onclick="cargarMas('${key}')">+ generar más</button>
      </div>
      <div class="tp-progreso hidden"><span></span></div>
      <div class="tipo-panel-items"></div>`;
    // Verificados arriba, comunes abajo — el mismo orden del reparto.
    const siguiente = key === "verificado"
      ? lista.querySelector('.tipo-panel[data-tipo="noverif"]')
      : null;
    lista.insertBefore(panel, siguiente || null);
  }
  return panel;
}

function _seccionItems(genero, tipo) {
  const key = (genero === "hombres" || genero === "mujeres") ? genero : "otros";
  const tipoKey = tipo === "noverif" ? "noverif" : "verificado";
  const lista = _panelTipo(tipoKey).querySelector(".tipo-panel-items");
  let sec = lista.querySelector(`.genero-seccion[data-genero="${key}"]`);
  if (!sec) {
    const meta = _SECCIONES.find(s => s.key === key);
    sec = document.createElement("div");
    sec.className = "genero-seccion";
    sec.dataset.genero = key;
    sec.innerHTML = `
      <div class="genero-seccion-header">
        <span class="gs-sec-icon">${meta.icon}</span>
        <span class="gs-sec-label">${meta.label}</span>
        <span class="gs-sec-sel hidden">0 sel.</span>
        <button type="button" class="gs-sec-all" onclick="toggleSeccion('${key}', '${tipoKey}')">Todos</button>
        <span class="gs-sec-count">0</span>
      </div>
      <div class="genero-seccion-items"></div>
      <div class="genero-seccion-empty">Todavía no hay comentarios de ${meta.label.toLowerCase()}</div>`;
    // Orden fijo en pantalla: Hombres → Mujeres → Sin especificar.
    const orden = _SECCIONES.map(s => s.key);
    const pos = orden.indexOf(key);
    const siguienteSec = [...lista.querySelectorAll(".genero-seccion")]
      .find(s => orden.indexOf(s.dataset.genero) > pos);
    lista.insertBefore(sec, siguienteSec || null);
  }
  return sec.querySelector(".genero-seccion-items");
}

// Renumera 1..N en el orden visual y actualiza el contador de cada sección.
// Devuelve el total de comentarios mostrados.
function _refrescarSecciones() {
  const lista = document.getElementById("lista-comentarios");
  let n = 0;
  // Los paneles vacíos no se muestran: recién aparecen cuando cae el primer
  // comentario de esa lista (o cuando el objetivo de la ficha dice que va).
  lista.querySelectorAll(".tipo-panel").forEach((panel) => {
    const total = panel.querySelectorAll(".comentario-item").length;
    const cnt = panel.querySelector(".tp-count");
    if (cnt) cnt.textContent = total;
    const objetivo = panel.dataset.tipo === "noverif" ? objetivoNV : objetivoV;
    panel.classList.toggle("hidden", total === 0 && !objetivo);
  });
  lista.querySelectorAll(".genero-seccion").forEach((sec) => {
    const items = sec.querySelectorAll(".comentario-item");
    const cnt = sec.querySelector(".gs-sec-count");
    if (cnt) cnt.textContent = items.length;
    // En mixto mantenemos Hombres y Mujeres visibles aunque estén vacías (para que
    // las 2 columnas estén desde el arranque). "otros" se oculta si queda vacía.
    // ...pero solo una vez que empezaron a llegar comentarios: antes de eso el
    // skeleton ya ocupa ese espacio y dos paneles "Todavía no hay..." vacíos
    // debajo se ven como un hueco muerto.
    const g = sec.dataset.genero;
    const hayAlguno = lista.querySelector(".comentario-item");
    const mantener = esMixto && hayAlguno && (g === "hombres" || g === "mujeres");
    sec.classList.toggle("hidden", items.length === 0 && !mantener);
    items.forEach((it) => {
      n++;
      const e = it.querySelector(".comentario-num");
      if (e) e.textContent = n;
    });
  });
  // 2 columnas (Hombres | Mujeres) dentro de cada panel, si el cliente es mixto
  // o si ese panel ya tiene los dos géneros.
  let mixto = esMixto;
  lista.querySelectorAll(".tipo-panel-items").forEach((cont) => {
    const hayH = cont.querySelector('.genero-seccion[data-genero="hombres"]:not(.hidden)');
    const hayM = cont.querySelector('.genero-seccion[data-genero="mujeres"]:not(.hidden)');
    const dos = esMixto || !!(hayH && hayM);
    cont.classList.toggle("lista-2col", dos);
    mixto = mixto || dos;
  });
  // En mixto la tarjeta rompe el ancho de .main y usa todo el ancho visible.
  const card = lista.closest(".comments-card");
  if (card) card.classList.toggle("comments-card--wide", mixto);
  // Los comentarios que van llegando también tienen que respetar el filtro
  // activo, si no aparecen items que no coinciden con lo buscado.
  marcarDuplicados();
  aplicarFiltroLista();
  return n;
}

// ── Repetidos ─────────────────────────────────────────────────────────────
// El stream ya descarta los idénticos de una misma tanda, pero "cargar más" y
// los comentarios agregados/editados a mano sí pueden repetir uno que ya está.
// Publicar el mismo texto dos veces en un post se nota, así que se marca.
function marcarDuplicados() {
  const items = document.querySelectorAll("#lista-comentarios .comentario-item");
  const vistos = new Map();
  let repes = 0;
  items.forEach((it) => {
    const txt = it.querySelector(".comentario-texto")?.textContent || "";
    const n = normComentario(txt);
    if (!n) return;
    const primero = vistos.get(n);
    if (primero === undefined) {
      vistos.set(n, it);
      it.classList.remove("comentario-item--dup");
      it.removeAttribute("title");
      return;
    }
    // Sólo se marca la repetición, no la primera aparición: esa es la buena.
    it.classList.add("comentario-item--dup");
    it.title = "Repetido: este texto ya está más arriba en la lista";
    repes++;
  });

  const btn = document.getElementById("btn-quitar-repes");
  if (btn) {
    btn.classList.toggle("hidden", repes === 0);
    btn.textContent = `Desmarcar ${repes} repetido${repes === 1 ? "" : "s"}`;
  }
}

// Desmarca las repeticiones y deja marcada la primera aparición de cada texto.
function desmarcarRepetidos() {
  document.querySelectorAll("#lista-comentarios .comentario-item--dup").forEach((it) => {
    const chk = it.querySelector("input");
    if (!chk || !chk.checked) return;
    chk.checked = false;
    it.classList.remove("selected");
  });
  actualizarConteo();
}

// ── Selección por rango (Shift+click) ─────────────────────────────────────
let ultimoItemTocado = null;

// El rango va en el orden VISUAL de la lista, salteando lo que esconde el
// filtro: marcar filas que no se están viendo sería una sorpresa desagradable.
function seleccionarRango(desde, hasta, marcar) {
  const visibles = [...document.querySelectorAll(
    "#lista-comentarios .comentario-item:not(.comentario-item--filtrado)"
  )];
  const a = visibles.indexOf(desde);
  const b = visibles.indexOf(hasta);
  if (a === -1 || b === -1) return;
  const [ini, fin] = a < b ? [a, b] : [b, a];
  for (let i = ini; i <= fin; i++) {
    const it = visibles[i];
    const chk = it.querySelector("input");
    if (!chk) continue;
    chk.checked = marcar;
    it.classList.toggle("selected", marcar);
  }
  actualizarConteo();
}

// ── Filtro de la lista ────────────────────────────────────────────────────
// Sólo esconde filas: no desmarca, no reordena y no toca la numeración, para
// que lo que se publica sea siempre lo seleccionado y no lo visible.
function _textoFiltro() {
  const el = document.getElementById("lista-filtro-input");
  return el ? el.value.trim().toLowerCase() : "";
}

function aplicarFiltroLista() {
  const q = _textoFiltro();
  const wrap = document.getElementById("lista-filtro");
  const lista = document.getElementById("lista-comentarios");
  if (!wrap || !lista) return;

  const items = lista.querySelectorAll(".comentario-item");
  // El filtro recién tiene sentido cuando hay lista; antes es un campo muerto.
  wrap.classList.toggle("hidden", items.length === 0);

  let visibles = 0;
  items.forEach((it) => {
    const txt = (it.querySelector(".comentario-texto")?.textContent || "").toLowerCase();
    const pasa = !q || txt.includes(q);
    it.classList.toggle("comentario-item--filtrado", !pasa);
    if (pasa) visibles++;
  });

  // Una sección sin ningún resultado estorba: se esconde mientras dure la
  // búsqueda (con el filtro vacío manda la lógica normal de _refrescarSecciones).
  if (q) {
    lista.querySelectorAll(".genero-seccion, .tipo-panel").forEach((cont) => {
      const hay = cont.querySelector(".comentario-item:not(.comentario-item--filtrado)");
      cont.classList.toggle("seccion--sin-resultados", !hay);
    });
  } else {
    lista.querySelectorAll(".seccion--sin-resultados")
      .forEach((c) => c.classList.remove("seccion--sin-resultados"));
  }

  const cnt = document.getElementById("lista-filtro-count");
  if (cnt) cnt.textContent = q ? `${visibles} de ${items.length}` : "";
  const btn = document.getElementById("lista-filtro-clear");
  if (btn) btn.classList.toggle("hidden", !q);
}

function filtrarLista() { aplicarFiltroLista(); }

function limpiarFiltroLista() {
  const el = document.getElementById("lista-filtro-input");
  if (!el) return;
  el.value = "";
  aplicarFiltroLista();
  el.focus();
}

// Filtro anti-duplicados de la tanda inicial: el modelo a veces repite el MISMO
// comentario dentro de una generación (sobre todo los cortos).
// A propósito acá se comparan solo textos IDÉNTICOS (normalizados), no paráfrasis:
// dentro de una tanda, variantes tipo "praying for you" / "praying for you brother"
// son comentarios válidos y filtrarlas recortaba demasiado el total.
// El filtro fuzzy (paráfrasis) se aplica en "Cargar más", que es donde molestaba.
let vistosStream = new Set();

function agregarComentarioFiltrado(texto, index) {
  if (generoDeHeader(texto)) { agregarComentario(texto, index); return; }
  const n = normComentario(texto);
  if (vistosStream.has(n)) return;
  vistosStream.add(n);
  agregarComentario(texto, index);
}

// A qué lista va el comentario que acaba de llegar: primero se completa el
// objetivo de verificados, después el de comunes, y lo que sobra se reparte
// alternado como reserva de las dos listas.
function _tipoParaNuevo() {
  if (tipoForzado) {
    if (tipoForzado === "verificado") asignadosV++; else asignadosNV++;
    return tipoForzado;
  }
  if (asignadosV < objetivoV) { asignadosV++; return "verificado"; }
  if (asignadosNV < objetivoNV) { asignadosNV++; return "noverif"; }
  // Sin objetivos cargados en la ficha no hay nada que repartir: todo entra
  // como verificado y el vendedor mueve lo que quiera, como venía siendo.
  if (!objetivoV && !objetivoNV) { asignadosV++; return "verificado"; }
  // Cubiertos los dos objetivos, lo que sobra queda de reserva, alternado.
  if (asignadosV - objetivoV <= asignadosNV - objetivoNV) { asignadosV++; return "verificado"; }
  asignadosNV++;
  return "noverif";
}

function agregarComentario(texto, index) {
  // Los encabezados de género (hombres:/mujeres:) marcan la sección: no se
  // muestran como comentarios, pero se guardan para reinyectarlos al enviar.
  const gen = generoDeHeader(texto);
  if (gen) {
    generoActual = generoFijo || gen;
    comentariosGenerados[index] = texto;
    generosGenerados[index] = "__header__";
    return;
  }

  const lista = document.getElementById("lista-comentarios");
  const i = index;
  comentariosGenerados[i] = texto;
  generosGenerados[i] = generoActual;
  if (tiposGenerados[i] === undefined) tiposGenerados[i] = _tipoParaNuevo();
  const tipo = tiposGenerados[i];
  // Dentro del objetivo de la ficha: viene marcado. El vendedor destilda lo que
  // no le guste en vez de tener que elegir 80 comentarios a mano.
  const autoElegido = !tipoForzado &&
    (tipo === "verificado" ? asignadosV <= objetivoV : asignadosNV <= objetivoNV);

  const item = document.createElement("div");
  item.className = "comentario-item" + (autoElegido ? " selected" : "");
  item.dataset.index = i;
  const switchGenero = generoActual
    ? `<button type="button" class="genero-switch genero-switch--${generoActual}" id="gen-${i}" title="Hombre / Mujer — click para cambiar" onclick="toggleGenero(event, ${i})"><span class="gs-knob">${generoActual === "hombres" ? "H" : "M"}</span></button>`
    : "";
  item.innerHTML = `
    <span class="comentario-num">${i + 1}</span>
    <input type="checkbox" id="chk-${i}" ${autoElegido ? "checked" : ""} onchange="onCheckChange(${i})" />
    <span class="comentario-texto" id="txt-${i}">${escapeHtml(texto)}</span>
    ${switchGenero}
    <button type="button" class="tipo-switch tipo-switch--${tipo}" id="tipo-${i}" title="Verificado / No verificado — click para cambiar" onclick="toggleTipo(event, ${i})"><span class="ts-knob">${tipo === "verificado" ? "V" : "NV"}</span></button>
    <button type="button" class="comentario-edit" title="Editar" onclick="editarComentario(event, ${i})">✎</button>
  `;
  // Doble click/tap sobre el texto abre la edición, además del lápiz: en mobile
  // el lápiz es un blanco chico y el texto es todo el ancho de la fila.
  item.addEventListener("dblclick", (e) => {
    if (!e.target.classList.contains("comentario-texto")) return;
    e.preventDefault();
    editarComentario(e, i);
  });
  item.addEventListener("click", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "BUTTON") return;
    if (e.target.classList.contains("comentario-texto") && e.target.isContentEditable) return;
    const chk = item.querySelector("input");
    // Shift+click: marca todo el bloque desde el último que se tocó. Elegir 40
    // comentarios seguidos de a un click era el trabajo más repetitivo acá.
    if (e.shiftKey && ultimoItemTocado && ultimoItemTocado !== item) {
      seleccionarRango(ultimoItemTocado, item, !chk.checked);
      ultimoItemTocado = item;
      return;
    }
    chk.checked = !chk.checked;
    item.classList.toggle("selected", chk.checked);
    ultimoItemTocado = item;
    actualizarConteo();
  });
  const skeleton = document.getElementById("skeleton-list");
  if (skeleton) skeleton.remove();
  // Ya hay algo que mirar: el aviso de "procesando" pierde sentido y su spinner
  // seguía girando arriba de la lista terminada.
  mostrarStep(null);

  // Va a la lista de su tipo y, dentro, a la sección de su género (las dos se
  // crean solas la primera vez).
  _seccionItems(generoActual, tipo).appendChild(item);

  // Numeración corrida 1..N en el orden visual (los encabezados hombres:/mujeres:
  // no son items, así que no deben dejar huecos en la numeración).
  const count = _refrescarSecciones();

  const counter = document.getElementById("comments-counter");
  if (counter) {
    counter.textContent = `${count} generado${count === 1 ? "" : "s"}`;
  }

  actualizarConteo();
  document.getElementById("comments-actions-bar").classList.remove("hidden");
}

function onCheckChange(index) {
  const chk = document.getElementById(`chk-${index}`);
  if (!chk) return;
  const item = chk.closest(".comentario-item");
  if (item) item.classList.toggle("selected", chk.checked);
  actualizarConteo();
}

// Alterna el género de un comentario (hombre azul / mujer rosa).
function toggleGenero(e, index) {
  e.stopPropagation();
  const nuevo = generosGenerados[index] === "mujeres" ? "hombres" : "mujeres";
  generosGenerados[index] = nuevo;
  const sw = document.getElementById(`gen-${index}`);
  if (sw) {
    sw.classList.toggle("genero-switch--hombres", nuevo === "hombres");
    sw.classList.toggle("genero-switch--mujeres", nuevo === "mujeres");
    const knob = sw.querySelector(".gs-knob");
    if (knob) knob.textContent = nuevo === "hombres" ? "H" : "M";
    // Al cambiar el género, el comentario se muda a la sección que corresponde
    // (si no, el agrupado quedaría inconsistente).
    const item = sw.closest(".comentario-item");
    if (item) {
      _seccionItems(nuevo, tiposGenerados[index]).appendChild(item);
      _refrescarSecciones();
    }
  }
}

// Fija el tipo de un comentario (verificado = producto 94, noverif = 95) y
// sincroniza el switch.
function _setTipo(index, nuevo) {
  const anterior = tiposGenerados[index];
  tiposGenerados[index] = nuevo;
  const sw = document.getElementById(`tipo-${index}`);
  if (sw) {
    sw.classList.toggle("tipo-switch--verificado", nuevo === "verificado");
    sw.classList.toggle("tipo-switch--noverif", nuevo === "noverif");
    const knob = sw.querySelector(".ts-knob");
    if (knob) knob.textContent = nuevo === "verificado" ? "V" : "NV";
    // Cambiar el tipo lo muda de lista: si no, el panel diría una cosa y el
    // switch otra.
    if (anterior !== nuevo) {
      const item = sw.closest(".comentario-item");
      if (item) {
        _seccionItems(generosGenerados[index], nuevo).appendChild(item);
        _refrescarSecciones();
      }
    }
  }
}

// Alterna un comentario entre verificado (94) y no verificado (95).
function toggleTipo(e, index) {
  e.stopPropagation();
  _setTipo(index, tiposGenerados[index] === "noverif" ? "verificado" : "noverif");
  actualizarConteo();
}

// Edición inline: el ✎ vuelve editable el texto; se guarda al salir o con Enter.
function editarComentario(e, index) {
  e.stopPropagation();
  const span = document.getElementById(`txt-${index}`);
  if (!span) return;
  if (span.isContentEditable) { span.blur(); return; }

  span.contentEditable = "true";
  span.classList.add("editando");
  span.focus();
  const range = document.createRange();
  range.selectNodeContents(span);
  const selc = window.getSelection();
  selc.removeAllRanges();
  selc.addRange(range);

  const guardar = () => {
    span.contentEditable = "false";
    span.classList.remove("editando");
    const nuevo = span.textContent.trim();
    if (nuevo) comentariosGenerados[index] = nuevo;
    else span.textContent = comentariosGenerados[index];
    span.removeEventListener("blur", guardar);
    span.removeEventListener("keydown", onKey);
  };
  const onKey = (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); span.blur(); }
    else if (ev.key === "Escape") { span.textContent = comentariosGenerados[index]; span.blur(); }
  };
  span.addEventListener("blur", guardar);
  span.addEventListener("keydown", onKey);
}

// Agregar un comentario a mano. Usa un modal propio en vez de prompt() nativo:
// prompt()/alert() congelan la pestaña entera (y bloquean por completo a los
// navegadores automatizados de QA). El género default es el del cliente (o
// hombre si es mixto); después se cambia con el switch azul/rosa del comentario.
function agregarComentarioManual() {
  const input = document.getElementById("agregar-input");
  input.value = "";
  document.getElementById("agregar-input-error").classList.add("hidden");
  show("agregar-overlay");
  setTimeout(() => input.focus(), 30);
}

function cerrarAgregarModal() {
  hide("agregar-overlay");
}

function confirmarAgregarComentario() {
  const input = document.getElementById("agregar-input");
  const texto = (input.value || "").trim();
  if (!texto) {
    document.getElementById("agregar-input-error").classList.remove("hidden");
    input.focus();
    return;
  }
  hide("agregar-overlay");

  const presentes = new Set(generosGenerados.filter(g => g === "hombres" || g === "mujeres"));
  let genero = null;
  if (presentes.size === 1) genero = [...presentes][0];
  else if (presentes.size >= 2) genero = "hombres";

  const i = comentariosGenerados.length;
  const prev = generoActual;
  generoActual = genero;
  agregarComentario(texto, i);
  generoActual = prev;

  const chk = document.getElementById(`chk-${i}`);
  if (chk) { chk.checked = true; onCheckChange(i); }
  const item = chk && chk.closest(".comentario-item");
  if (item) item.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function copiarComentario(e, index) {
  e.stopPropagation();
  navigator.clipboard.writeText(comentariosGenerados[index] || "").then(() => {
    const toast = document.getElementById("copy-toast");
    toast.classList.remove("hidden");
    toast.classList.add("show");
    setTimeout(() => {
      toast.classList.remove("show");
      setTimeout(() => toast.classList.add("hidden"), 200);
    }, 1600);
  });
}

function finalizarStream(meta) {
  generando = false;
  mostrarScrape(meta);
  mostrarEstadoTranscripcion(meta.transcription);
  esperandoTranscripcion = false;
  if (pendingComentarios.length > 0) {
    pendingComentarios.forEach((e) => { ocultarChunk(); agregarComentarioFiltrado(e.texto, e.index); });
    pendingComentarios = [];
  }
  actualizarConteo();
  finalizarChunk();
  hide("loading-overlay");
  document.getElementById("stream-status").textContent = "";
  document.getElementById("status-listo").classList.remove("hidden");
  const btnCargar = document.getElementById("btn-cargar-mas");
  if (btnCargar) btnCargar.classList.remove("hidden");
}

// tipo: "verificado" | "noverif" para que la tanda nueva caiga entera en esa
// lista ("generame más comunes"). Sin tipo, se reparte como la tanda inicial.
async function cargarMas(tipo) {
  const btn = tipo
    ? document.querySelector(`.tipo-panel[data-tipo="${tipo}"] .tp-mas`)
    : document.getElementById("btn-cargar-mas");
  const textoBtn = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Generando…"; }
  tipoForzado = tipo || null;

  try {
    // Le mandamos al backend los comentarios ya generados (sin encabezados) para
    // que la nueva tanda no los repita ni parafrasee.
    const evitar = comentariosGenerados.filter(c => c && !generoDeHeader(c));
    const resp = await fetch("/api/procesar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, evitar, keyword: currentKeyword }),
    });
    const data = await resp.json();
    if (data.error) throw new Error(data.error);

    const prevJobId = currentJobId;
    currentJobId = data.job_id;
    streamOffset = 0;
    streamProgresoOffset = 0;
    pendingComentarios = [];

    // Offset global para no pisar indices existentes
    const baseIndex = comentariosGenerados.length;

    // Red de seguridad del front: aunque el backend ya recibe la lista a evitar,
    // filtramos acá los casi-duplicados que se le puedan escapar (paráfrasis,
    // abreviaturas, emoji de más). Comparamos contra todo lo ya mostrado.
    const vistos = comentariosGenerados.filter(c => c && !generoDeHeader(c)).map(infoComentario);

    const url = `/api/stream/${currentJobId}?offset=0&progreso_offset=0`;
    // Misma señal que el stream principal: si el vendedor publica mientras esto
    // corre, cancelarGeneracion() lo corta también.
    streamCancelado = false;
    streamAbort = new AbortController();
    const resp2 = await fetch(url, { signal: streamAbort.signal });
    const reader = resp2.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      if (streamCancelado) break;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let evento;
        try { evento = JSON.parse(line.slice(6)); } catch { continue; }
        if (evento.tipo === "comentario") {
          // Los encabezados de género se procesan siempre (marcan la sección);
          // los comentarios reales, solo si no son un casi-duplicado de lo cargado.
          if (generoDeHeader(evento.texto)) {
            agregarComentario(evento.texto, baseIndex + evento.index);
          } else {
            const info = infoComentario(evento.texto);
            if (!esCasiDuplicado(info, vistos)) {
              vistos.push(info);
              agregarComentario(evento.texto, baseIndex + evento.index);
            }
          }
        } else if (evento.tipo === "listo" || evento.tipo === "error") {
          break;
        }
      }
    }
  } catch (e) {
    if (!streamCancelado) console.error("cargarMas error:", e);
  }

  tipoForzado = null;
  actualizarConteo();
  if (btn) { btn.disabled = false; btn.textContent = textoBtn || "+ Cargar más"; }
}



function contarSeleccionados() {
  return [...document.querySelectorAll("#lista-comentarios input[type=checkbox]")].filter(c => c.checked).length;
}

// Selecciona / deselecciona toda una columna de género de un click: con 2
// columnas de ~30 comentarios, tildarlos uno por uno era lo más tedioso.
function toggleSeccion(key, tipo) {
  // Hay una sección por género DENTRO de cada lista (V y NV): sin el tipo, el
  // botón de una tildaba la de la otra.
  const sec = document.querySelector(
    `.tipo-panel[data-tipo="${tipo || "verificado"}"] .genero-seccion[data-genero="${key}"]`);
  if (!sec) return;
  const checks = [...sec.querySelectorAll("input[type=checkbox]")];
  if (!checks.length) return;
  const marcar = checks.some(c => !c.checked);   // si falta alguno → marcar todos
  checks.forEach((c) => {
    c.checked = marcar;
    c.closest(".comentario-item").classList.toggle("selected", marcar);
  });
  actualizarConteo();
}

// Contador "N elegidos" de cada lista (V / NV) + estado de su botón Todos.
function _refrescarConteoPaneles() {
  document.querySelectorAll("#lista-comentarios .tipo-panel").forEach((panel) => {
    const checks = [...panel.querySelectorAll("input[type=checkbox]")];
    const sel = checks.filter(c => c.checked).length;
    const badge = panel.querySelector(".tp-sel");
    if (badge) {
      const objetivo = panel.dataset.tipo === "noverif" ? objetivoNV : objetivoV;
      badge.textContent = objetivo ? `${sel} de ${objetivo} elegidos` : `${sel} elegidos`;
      badge.classList.toggle("hidden", checks.length === 0);
      // Verde cuando coincide con lo que pide la ficha del cliente.
      badge.classList.toggle("tp-sel--ok", !!objetivo && sel === objetivo);
    }
    const btn = panel.querySelector(".tp-all");
    if (btn) {
      const todos = checks.length > 0 && sel === checks.length;
      btn.textContent = todos ? "Ninguno" : "Todos";
      btn.disabled = checks.length === 0;
    }
    // Barra de avance hacia lo que pide la ficha del cliente: el número solo
    // ("12 de 40") obliga a hacer la cuenta cada vez que se marca uno.
    const barra = panel.querySelector(".tp-progreso");
    if (barra) {
      const objetivo = panel.dataset.tipo === "noverif" ? objetivoNV : objetivoV;
      barra.classList.toggle("hidden", !objetivo || checks.length === 0);
      if (objetivo) {
        const pct = Math.min(100, Math.round((sel / objetivo) * 100));
        barra.firstElementChild.style.width = pct + "%";
        // Pasarse del objetivo no es un error, pero tiene que verse distinto de
        // haberlo cumplido justo.
        barra.classList.toggle("tp-progreso--ok", sel === objetivo);
        barra.classList.toggle("tp-progreso--over", sel > objetivo);
      }
    }
  });
}

// Contador "N sel." por sección + estado del botón Todos.
function _refrescarConteoSecciones() {
  _refrescarConteoPaneles();
  document.querySelectorAll("#lista-comentarios .genero-seccion").forEach((sec) => {
    const checks = [...sec.querySelectorAll("input[type=checkbox]")];
    const sel = checks.filter(c => c.checked).length;
    const badge = sec.querySelector(".gs-sec-sel");
    if (badge) {
      badge.textContent = `${sel} sel.`;
      badge.classList.toggle("hidden", sel === 0);
    }
    const btn = sec.querySelector(".gs-sec-all");
    if (btn) {
      const todos = checks.length > 0 && sel === checks.length;
      btn.textContent = todos ? "Ninguno" : "Todos";
      btn.classList.toggle("gs-sec-all--on", todos);
      btn.disabled = checks.length === 0;
    }
  });
}

// Marca o desmarca toda una lista de una.
function togglePanel(tipo) {
  const panel = document.querySelector(`.tipo-panel[data-tipo="${tipo}"]`);
  if (!panel) return;
  const checks = [...panel.querySelectorAll("input[type=checkbox]")];
  const marcar = checks.some(c => !c.checked);
  checks.forEach((c) => {
    c.checked = marcar;
    c.closest(".comentario-item").classList.toggle("selected", marcar);
  });
  actualizarConteo();
}

// Cuenta los comentarios seleccionados separados por tipo (V / NV).
function contarPorTipo() {
  let verif = 0, noverif = 0;
  document.querySelectorAll("#lista-comentarios input[type=checkbox]:checked").forEach((c) => {
    const idx = parseInt(c.closest(".comentario-item").dataset.index, 10);
    if (Number.isNaN(idx)) return;
    if (tiposGenerados[idx] === "noverif") noverif++; else verif++;
  });
  return { verif, noverif };
}

function actualizarConteo() {
  const sel = contarSeleccionados();
  _refrescarConteoSecciones();
  const label = document.getElementById("count-label");
  if (label) {
    // Desglose V/NV a la vista: es lo que hay que chequear antes de publicar
    // (y lo que define en cuántas órdenes se parte).
    const { verif, noverif } = contarPorTipo();
    label.textContent = sel === 0
      ? "0 seleccionados"
      : `${verif} verificados · ${noverif} comunes`;
    label.classList.toggle("count-label--lleno", sel > 0);   // pill amarilla con selección
  }
  const btnPublicar = document.getElementById("btn-publicar");
  if (btnPublicar) btnPublicar.disabled = sel === 0;
  actualizarPanel();
}

function actualizarPanel() {
  const items = [...document.querySelectorAll("#lista-comentarios .comentario-item")];
  const seleccionados = items.filter(i => i.querySelector("input").checked);
  const panel = document.getElementById("panel-seleccionados");
  const lista = document.getElementById("panel-lista");
  const count = document.getElementById("panel-count");

  if (!panel || !lista || !count) return;

  if (seleccionados.length === 0) {
    panel.classList.add("hidden");
    return;
  }

  panel.classList.remove("hidden");
  count.textContent = `${seleccionados.length}`;

  lista.innerHTML = seleccionados.map((item, i) => {
    const texto = item.querySelector(".comentario-texto").textContent;
    const index = item.dataset.index;
    return `<div class="panel-item">
      <span class="panel-num">${i + 1}</span>
      <span class="panel-texto">${escapeHtml(texto)}</span>
      <button class="panel-remove" onclick="quitarSeleccion(${index})">×</button>
    </div>`;
  }).join("");
}

function quitarSeleccion(index) {
  const chk = document.getElementById(`chk-${index}`);
  if (!chk) return;
  chk.checked = false;
  chk.closest(".comentario-item").classList.remove("selected");
  actualizarConteo();
}

function seleccionarTodos() {
  document.querySelectorAll("#lista-comentarios input[type=checkbox]").forEach((c) => {
    c.checked = true;
    c.closest(".comentario-item").classList.add("selected");
  });
  actualizarConteo();
}

// Llena los casilleros de cantidad con lo configurado en la ficha del cliente
// (rango min-max → un número al azar adentro, así no manda siempre lo mismo).
// Sin cliente o sin rango cargado quedan en 0 y se completan a mano. Se pisan
// SIEMPRE: si no, el post de un cliente arrastraría la cantidad del anterior.
function _autocompletarComentarios(clienteLabel) {
  const cfg = (window._clientRanges || {}).comentarios || {};
  const tirar = (r) => {
    const mn = parseInt(r?.min, 10), mx = parseInt(r?.max, 10);
    if (!Number.isFinite(mn) || !Number.isFinite(mx)) return null;
    return mn + Math.floor(Math.random() * (mx - mn + 1));
  };
  const verif = tirar(cfg.verificados);
  const comunes = tirar(cfg.comunes);

  const set = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = v == null ? 0 : v;
  };
  set("cant-verif", verif);
  set("cant-noverif", comunes);

  // Estos son los objetivos del reparto automático: la generación va a marcar
  // sola esa cantidad de cada tipo. Sin ficha cargada no se reparte nada y el
  // vendedor elige a mano, como antes.
  objetivoV = verif || 0;
  objetivoNV = comunes || 0;
  asignadosV = 0;
  asignadosNV = 0;

  // El vendedor tiene que saber que el número no lo puso él, y de dónde salió.
  const hint = document.getElementById("reparto-hint");
  if (hint) {
    hint.textContent = (verif == null && comunes == null)
      ? "Elige al azar cuáles de la lista y los marca por vos"
      : `Cantidad de la ficha de ${clienteLabel || "el cliente"} — podés cambiarla`;
    hint.classList.toggle("reparto-hint--auto", verif != null || comunes != null);
  }
}

// Tomás: contar a mano y togglear el V/NV de a uno para llegar a "60 verificados
// + 80 comunes" es un viaje. Con esto se pide la cantidad exacta de cada tipo y
// el reparto lo hace solo: elige al azar CUÁLES comentarios van (para que no
// mande siempre los mismos) pero respeta al pie la cantidad pedida.
function repartirPorTipo() {
  const msg = document.getElementById("turnos-msg");
  const error = (txt) => {
    if (msg) { msg.textContent = txt; msg.classList.remove("hidden"); }
  };
  if (msg) msg.classList.add("hidden");

  const leer = (id) => {
    const raw = parseInt(document.getElementById(id)?.value, 10);
    return Math.max(0, Number.isFinite(raw) ? raw : 0);
  };
  const pedido = { verificado: leer("cant-verif"), noverif: leer("cant-noverif") };

  if (pedido.verificado + pedido.noverif === 0) {
    return error("Escribí cuántos comentarios verificados y cuántos comunes querés mandar.");
  }
  if (pedido.noverif > TURNOS_MAX) {
    return error(`Los comunes tienen un tope de ${TURNOS_MAX} por día (40 + 40) y pediste ${pedido.noverif}. Bajá a ${TURNOS_MAX} o menos.`);
  }

  // Cada lista se reparte por su cuenta: se eligen al azar CUÁLES de esa lista
  // van (para no mandar siempre los mismos), respetando la cantidad pedida.
  for (const [tipo, n] of Object.entries(pedido)) {
    const panel = document.querySelector(`.tipo-panel[data-tipo="${tipo}"]`);
    const items = panel ? [...panel.querySelectorAll(".comentario-item")] : [];
    const nombre = tipo === "verificado" ? "verificados" : "comunes";
    if (n > items.length) {
      return error(`Pediste ${n} ${nombre} y hay ${items.length} generados. Tocá "+ generar más" en esa lista, o bajá la cantidad.`);
    }
    const orden = items.map((_, k) => k);
    for (let k = orden.length - 1; k > 0; k--) {
      const j = Math.floor(Math.random() * (k + 1));
      [orden[k], orden[j]] = [orden[j], orden[k]];
    }
    orden.forEach((k, pos) => {
      const item = items[k];
      const elegido = pos < n;
      const chk = item.querySelector("input[type=checkbox]");
      if (chk) chk.checked = elegido;
      item.classList.toggle("selected", elegido);
    });
  }

  actualizarConteo();
}

function deseleccionarTodos() {
  document.querySelectorAll("#lista-comentarios input[type=checkbox]").forEach((c) => {
    c.checked = false;
    c.closest(".comentario-item").classList.remove("selected");
  });
  actualizarConteo();
}

function publicar() {
  // Ya eligió los comentarios: la IA no tiene que seguir generando.
  cancelarGeneracion();
  irAOrdenes();
}

// ── Cache de productos y nombre de red social ────────────────────────────────
const _productosCache = {};
const _nombreRedCache = {};
let _currentNombreRed = "Instagram";

// ── Mapa de redes sociales ───────────────────────────────────────────────────
const RS_META = {
  "1":  { icon: "📸", slug: "instagram", label: "Instagram", color: "#E1306C" },
  "2":  { icon: "🎵", slug: "tiktok",    label: "TikTok",    color: "#69C9D0" },
  "4":  { icon: "🎧", slug: "spotify",   label: "Spotify",   color: "#1DB954" },
  "5":  { icon: "📘", slug: "facebook",  label: "Facebook",  color: "#1877F2" },
  "6":  { icon: "🎛️", slug: "",          label: "Panel",     color: "#a0a0a0" },
  "11": { icon: "▶️",  slug: "youtube",  label: "YouTube",   color: "#FF0000" },
  "12": { icon: "🐦", slug: "x",         label: "Twitter/X", color: "#ffffff" },
  "13": { icon: "🔍", slug: "google",    label: "Google",    color: "#4285F4" },
  "14": { icon: "👻", slug: "snapchat",  label: "Snapchat",  color: "#FFFC00" },
};

// ── Estado de órdenes ────────────────────────────────────────────────────────
let ordenes = [];
let comentariosParaPublicar = [];

// ── Turnos de comentarios no verificados ─────────────────────────────────────
// Facu: los no-verif tienen dos turnos (mañana 09-15:30 / tarde 15:30-23), 40 c/u.
// Al publicar se reparten 50/50: una tanda al turno actual y la otra programada
// al próximo turno (hora fija 10:00 / 19:00, AR = hora local del navegador).
const TURNOS_MAX = 80;

function _fmtFechaCRM(d) {
  const p = x => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function _labelFecha(d) {
  return d.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}
// Devuelve [spec1, spec2] con {cuando, cuandoLabel, fechaProgramada}. spec1 es la
// tanda que va primero (y recibe el comentario de más si la cantidad es impar).
function _turnosPlan() {
  const now = new Date();
  const min = now.getHours() * 60 + now.getMinutes();
  const MANANA = 9 * 60, TARDE = 15 * 60 + 30, NOCHE = 23 * 60;   // 09:00 / 15:30 / 23:00
  const aHora = (h, m, addDay = 0) => {
    const d = new Date(now); d.setHours(h, m, 0, 0); if (addDay) d.setDate(d.getDate() + addDay); return d;
  };
  const prox = (h) => { const d = aHora(h, 0); if (d <= now) d.setDate(d.getDate() + 1); return d; };
  const ahora = { cuando: "ahora", cuandoLabel: "Ahora", fechaProgramada: "" };
  const prog = (d) => ({ cuando: "programar", cuandoLabel: _labelFecha(d), fechaProgramada: _fmtFechaCRM(d) });

  if (min >= MANANA && min < TARDE) {
    // Turno mañana: una tanda ahora, la otra hoy 19:00.
    return [ahora, prog(aHora(19, 0))];
  }
  if (min >= TARDE && min < NOCHE) {
    // Turno tarde: una tanda ahora, la otra mañana 10:00.
    return [ahora, prog(aHora(10, 0, 1))];
  }
  // Fuera de horario (23:00-09:00): las dos programadas (próximo 10:00 y 19:00).
  return [prog(prox(10)), prog(prox(19))];
}

async function irAOrdenes() {
  // Seleccionamos por data-index (no por posición del DOM), porque la lista se
  // muestra mezclada visualmente.
  const idxSeleccionados = [...document.querySelectorAll("#lista-comentarios input[type=checkbox]:checked")]
    .map(c => parseInt(c.closest(".comentario-item").dataset.index, 10))
    .filter(i => !Number.isNaN(i));
  if (idxSeleccionados.length === 0) return;

  // Reconstruye la lista con los encabezados de género (hombres:/mujeres:) para
  // un conjunto de índices. Los encabezados NO cuentan como comentarios; el
  // backend re-mezcla dentro de cada sección.
  function reconstruir(indices) {
    const sel = indices.map(i => ({ texto: comentariosGenerados[i], genero: generosGenerados[i] }));
    const mujeres   = sel.filter(s => s.genero === "mujeres").map(s => s.texto);
    const hombres   = sel.filter(s => s.genero === "hombres").map(s => s.texto);
    const sinGenero = sel.filter(s => s.genero !== "mujeres" && s.genero !== "hombres").map(s => s.texto);
    const payload = [];
    if (mujeres.length) payload.push("mujeres:", ...mujeres);
    if (hombres.length) payload.push("hombres:", ...hombres);
    payload.push(...sinGenero);
    return { payload, cantidad: sel.length };  // cantidad = comentarios reales, sin headers
  }

  const idxVerif   = idxSeleccionados.filter(i => tiposGenerados[i] !== "noverif");
  const idxNoVerif = idxSeleccionados.filter(i => tiposGenerados[i] === "noverif");

  // Los no-verificados SIEMPRE se reparten en turnos (mañana/tarde), sin toggle.
  // Tope de no-verif por día (40 mañana + 40 tarde).
  const turnosMsg = document.getElementById("turnos-msg");
  if (turnosMsg) turnosMsg.classList.add("hidden");
  if (idxNoVerif.length > TURNOS_MAX) {
    if (turnosMsg) {
      turnosMsg.textContent = `Podés mandar hasta ${TURNOS_MAX} no verificados por día (40 + 40). Tenés ${idxNoVerif.length} seleccionados — sacá ${idxNoVerif.length - TURNOS_MAX}.`;
      turnosMsg.classList.remove("hidden");
    }
    return;   // no armamos órdenes hasta que baje del tope
  }

  // Conservar cualquier orden extra ya cargada (likes, views, etc.);
  // reemplazamos las de comentarios (pueden ser 2: verificados y no verificados).
  ordenes = ordenes.filter(o => o.tipo !== "comentarios");
  comentariosParaPublicar = [];
  let _oid = Date.now();

  const pushOrdenComentarios = (indices, productoId, productoNombre, spec, turno) => {
    const { payload, cantidad } = reconstruir(indices);
    comentariosParaPublicar.push(...payload);
    ordenes.push({
      id: _oid++,
      redsocial: "Instagram",
      redsocialId: "1",
      productoId,
      productoNombre,
      cantidad,
      link: currentUrl,
      cuando: spec.cuando,
      cuandoLabel: spec.cuandoLabel,
      fechaProgramada: spec.fechaProgramada,
      obs: "",
      tipo: "comentarios",
      comentarios: payload,   // cada orden lleva SU propia lista
      turno,                  // "1/2" | "2/2" | null
    });
  };

  const AHORA = { cuando: "ahora", cuandoLabel: "Ahora", fechaProgramada: "" };

  // Verificados: siempre una orden "ahora".
  if (idxVerif.length) {
    pushOrdenComentarios(idxVerif, 94, "Comentarios Reales Verificados", AHORA, null);
  }

  // No verificados: SIEMPRE repartidos 50/50 en turnos (mañana/tarde). Con un
  // solo comentario va una única tanda (no se puede partir en dos).
  if (idxNoVerif.length) {
    const [spec1, spec2] = _turnosPlan();
    const mitad = Math.ceil(idxNoVerif.length / 2);   // impar → la de más va en la 1ra tanda
    const hayDos = idxNoVerif.length > mitad;
    pushOrdenComentarios(idxNoVerif.slice(0, mitad), 95, "Comentarios Reales", spec1, hayDos ? "1/2" : null);
    if (hayDos) {
      pushOrdenComentarios(idxNoVerif.slice(mitad), 95, "Comentarios Reales", spec2, "2/2");
    }
  }

  // Info del post
  const clientName = document.getElementById("client-badge").textContent || "—";
  document.getElementById("ordenes-post-url").textContent = currentUrl;
  document.getElementById("ordenes-client").textContent = clientName;
  document.getElementById("ordenes-hero-avatar").textContent = clientName.charAt(0).toUpperCase();
  document.getElementById("orden-link").value = currentUrl;

  await actualizarProductos();
  // Órdenes de tráfico precreadas con los rangos configurados (del cliente si el
  // post es de uno, del genérico si no). Va después de actualizarProductos
  // porque necesita el catálogo para resolver el producto de cada tipo.
  await _precrearOrdenesDeRangos();
  renderOrdenes();
  prepararVentaPicker();

  // Ocultar panel de seleccionados y counter al pasar al form
  document.getElementById("panel-seleccionados").classList.add("hidden");
  document.getElementById("comments-counter").classList.add("hidden");
  document.getElementById("status-listo").classList.add("hidden");

  hide("step-comentarios");
  show("step-ordenes");
}

async function actualizarProductos() {
  const rsId = document.getElementById("orden-redsocial").value;
  const select = document.getElementById("orden-producto");

  select.innerHTML = '<option value="" disabled selected>Cargando...</option>';
  select.disabled = true;
  clearFieldError("orden-producto");
  document.getElementById("opt-split3").style.display = "none";
  document.getElementById("opt-split5").style.display = "none";
  document.getElementById("orden-demora-badge")?.classList.add("hidden");
  document.getElementById("orden-costo-badge")?.classList.add("hidden");

  // Obtener nombre de la red social en paralelo con los productos
  const [, ] = await Promise.all([
    (async () => {
      try {
        if (!_nombreRedCache[rsId]) {
          const r = await fetch(`/api/nombre_red?red=${rsId}`);
          const data = await r.json();
          _nombreRedCache[rsId] = data?.nombres?.red || "";
        }
        _currentNombreRed = _nombreRedCache[rsId] || "Instagram";
      } catch { _currentNombreRed = "Instagram"; }
    })(),
    (async () => {
      try {
        if (!_productosCache[rsId]) {
          const resp = await fetch(`/api/productos?rrss=${rsId}`);
          if (!resp.ok) throw new Error("Error al cargar productos");
          const data = await resp.json();
          if (data.error) throw new Error(data.error);
          _productosCache[rsId] = Object.entries(data).map(([grupo, items]) => ({
            grupo,
            items: items.map(p => ({ id: p.id, nombre: p.label })),
          }));
        }

        const grupos = _productosCache[rsId];
        select.innerHTML = '<option value="" disabled selected>Seleccione</option>';
        for (const grupo of grupos) {
          const og = document.createElement("optgroup");
          og.label = grupo.grupo;
          for (const item of grupo.items) {
            const opt = document.createElement("option");
            opt.value = item.id;
            opt.textContent = item.nombre;
            og.appendChild(opt);
          }
          select.appendChild(og);
        }
      } catch (e) {
        select.innerHTML = '<option value="" disabled selected>Error al cargar</option>';
        console.error("actualizarProductos:", e);
      }
    })(),
  ]);

  select.disabled = false;
}

async function obtenerDemora(productoNombre) {
  const badge = document.getElementById("orden-demora-badge");
  badge.classList.add("hidden");
  try {
    const r = await fetch(`/api/demora?redsocial=${encodeURIComponent(_currentNombreRed)}&producto=${encodeURIComponent(productoNombre)}`);
    const data = await r.json();
    const demora = data.demora?.trim();
    if (demora) {
      badge.textContent = `⏱ ${demora} min`;
      badge.classList.remove("hidden");
    }
  } catch { /* silencioso */ }
}

let _lastCosto = null; // último costo fetched, para guardarlo en la orden
let _lastCantMin = null; // último cantmin fetched, para validar el split de intervalo
let _costoTimer = null;
async function obtenerCosto() {
  clearTimeout(_costoTimer);
  _costoTimer = setTimeout(_fetchCosto, 300);
}

async function _fetchCosto() {
  const rsId       = document.getElementById("orden-redsocial").value;
  const prodId     = document.getElementById("orden-producto").value;
  const hint       = document.getElementById("orden-cantidad-hint");
  const cantEl     = document.getElementById("orden-cantidad");
  const costoField = document.getElementById("orden-costo-display");
  if (costoField) costoField.value = "";
  if (!prodId) return;

  try {
    const r = await fetch("/api/costo_trafico", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        redsocial: rsId,
        producto: prodId,
        cant_solicitada: cantEl.value ? parseInt(cantEl.value) : null,
      }),
    });
    const data = await r.json();

    // Rango min/max como hint + ajuste automático de cantidad
    if (data.cantmin || data.cantmax) {
      const min = parseInt(data.cantmin) || 0;
      const max = parseInt(data.cantmax) || 0;
      _lastCantMin = min;
      hint.textContent = `Min: ${min.toLocaleString()} — Max: ${max.toLocaleString()}`;
      hint.classList.remove("hidden");

      const cant = parseInt(cantEl.value) || 0;
      if (min === max && min > 0) {
        cantEl.value = min;
      } else if (cant < min) {
        cantEl.value = min;
      } else if (max > 0 && cant > max) {
        cantEl.value = max;
      }
      clearFieldError("orden-cantidad");
    }

    _lastCosto = data.costoTrafico != null ? parseFloat(data.costoTrafico) : null;

    // Mostrar costo en el campo readonly
    if (costoField && data.costoTrafico != null) {
      const costo = parseFloat(data.costoTrafico);
      costoField.value = costo === 0 ? "$0.00" : `$${costo.toFixed(4)}`;
    }
  } catch { /* silencioso */ }
}

function onProductoChange() {
  clearFieldError("orden-producto");
  const prodSelect = document.getElementById("orden-producto");
  const prodId = parseInt(prodSelect.value);
  const prodNombre = (prodSelect.options[prodSelect.selectedIndex]?.text || "").toLowerCase();
  const isComment = (prodNombre.includes("comentario") || prodNombre.includes("comment")) && !prodNombre.includes("custom");
  document.getElementById("opt-split3").style.display = isComment ? "none" : "";
  document.getElementById("opt-split5").style.display = isComment ? "none" : "";
  const cuandoVal = document.getElementById("orden-cuando-val")?.value;
  if (cuandoVal === "split3" || cuandoVal === "split5") {
    document.getElementById("orden-cuando-val").value = "ahora";
    document.querySelectorAll(".cuando-pill").forEach(b => b.classList.remove("cuando-pill--active"));
    const pillAhora = document.querySelector('.cuando-pill[data-value="ahora"]');
    if (pillAhora) pillAhora.classList.add("cuando-pill--active");
    document.getElementById("orden-fecha-wrap").classList.add("hidden");
  }
  const productoNombre = prodSelect.options[prodSelect.selectedIndex]?.text || "";
  obtenerDemora(productoNombre);

  // TAREA 6: si el producto es like/view/share y el cliente tiene rango, mostramos
  // el botón 🎲 y autocompletamos la cantidad (solo si está vacía, para no pisar
  // lo que el vendedor haya cargado al editar una orden).
  const r = _rangoDelProducto();
  const cantEl = document.getElementById("orden-cantidad");
  if (r && !cantEl.value) rollCantidad();

  obtenerCosto();
}

// ── Cantidad automática por rango (TAREA 6) ──────────────────────────────────
// Tipos de producto con rango configurable por cliente. El orden importa para
// el select y para las órdenes precreadas.
const RANGE_KEYS = ["likes", "views", "shares", "reposts", "saves", "reach"];

function _tipoProducto(nombre) {
  const n = (nombre || "").toLowerCase();
  if (n.includes("like") || n.includes("me gusta")) return "likes";
  if (n.includes("view") || n.includes("reproduc") || n.includes("visualiz") || n.includes("vista")) return "views";
  // Reposts antes que shares: en el CRM son "Reposteos"/"Repost" y algunos
  // nombres los mezclan con "compartir".
  if (n.includes("repost") || n.includes("reposte") || n.includes("requeteo")) return "reposts";
  if (n.includes("save") || n.includes("guardad") || n.includes("guardar")) return "saves";
  if (n.includes("reach") || n.includes("alcance")) return "reach";
  if (n.includes("share") || n.includes("compart")) return "shares";
  return null;
}

// Un tipo puede tener varias entradas (dos calidades de likes en el mismo post).
// Las fichas viejas guardaron un solo objeto por tipo: se lee igual.
function _entradasDeRango(v) {
  if (Array.isArray(v)) return v;
  return v ? [v] : [];
}

function _rangoDelProducto() {
  const prodSelect = document.getElementById("orden-producto");
  const nombre = prodSelect.options[prodSelect.selectedIndex]?.text || "";
  const tipo = _tipoProducto(nombre);
  const ranges = window._clientRanges || {};
  const entradas = _entradasDeRango(tipo && ranges[tipo]);
  // Con varias calidades del mismo tipo, manda la del producto elegido; si el
  // vendedor eligió una variante sin rango propio, cae en la primera del tipo.
  const prodId = String(prodSelect.value || "");
  const r = entradas.find(e => String(e.prod_id || "") === prodId) || entradas[0] || null;
  const btn = document.getElementById("btn-roll-cantidad");
  if (btn) btn.style.display = r ? "" : "none";
  return r;
}

// ── Campaña de la que sale la plata cuando el post no es de ningún cliente ──
// Con cliente, el backend la resuelve solo (asignada > última del perfil). Sin
// cliente no hay nada que deducir: antes caía en la campaña por defecto de la
// cuenta sin avisar, ahora se elige acá y viaja en el envío.
// Saldo de cada campaña (idventa -> disponible), para mostrarlo al elegirla.
let _ventasSaldo = {};

function _ventaPickerMsg(texto, falta) {
  const msg = document.getElementById("venta-picker-msg");
  if (!msg) return;
  msg.textContent = texto || "";
  msg.classList.toggle("hidden", !texto);
  if (!falta) document.getElementById("venta-picker").classList.remove("venta-picker--falta");
}

function prepararVentaPicker() {
  const box = document.getElementById("venta-picker");
  if (!box) return;
  const sel = document.getElementById("venta-picker-select");
  const retry = document.getElementById("venta-picker-retry");
  const owner = document.getElementById("venta-picker-owner");
  box.classList.remove("venta-picker--falta");
  _ventaPickerMsg("");
  _mostrarSaldoVenta("");

  if (window._clienteAsignado) {
    box.classList.add("hidden");
    window._ventaElegida = "";
    return;
  }

  box.classList.remove("hidden");
  // De qué cuenta es el post: lo primero que uno mira para entender el aviso.
  if (owner) {
    owner.textContent = window._clientIg ? `@${window._clientIg}` : "";
    owner.classList.toggle("hidden", !window._clientIg);
  }
  retry.classList.add("hidden");
  sel.disabled = true;
  sel.innerHTML = `<option value="">Cargando campañas…</option>`;
  fetch("/api/ventas")
    .then(r => r.json())
    .then(d => {
      const ventas = d.ventas || [];
      sel.disabled = false;
      _ventasSaldo = {};
      ventas.forEach(v => { _ventasSaldo[String(v.idventa)] = parseFloat(v.disponible) || 0; });
      if (!ventas.length) {
        sel.innerHTML = `<option value="">No hay campañas disponibles</option>`;
        sel.disabled = true;
        retry.classList.remove("hidden");
        _ventaPickerMsg("No encontramos campañas en tu cuenta del CRM. Creá una o pedile al admin que te asigne el cliente.", true);
        return;
      }
      const saldo = (v) => `$${(parseFloat(v.disponible) || 0).toFixed(2)}`;
      sel.innerHTML = `<option value="">— Elegí una campaña —</option>` +
        ventas.map(v => `<option value="${escapeHtml(v.idventa)}">` +
          `#${escapeHtml(v.idventa)} · ${saldo(v)} · ${escapeHtml(v.nombre)}` +
          `${v.activa ? "" : " (vieja)"}</option>`).join("");
      // Si ya había una elegida en este post, la mantenemos.
      if (window._ventaElegida) { sel.value = window._ventaElegida; _mostrarSaldoVenta(window._ventaElegida); }
    })
    .catch(() => {
      sel.innerHTML = `<option value="">No pude leer las campañas</option>`;
      sel.disabled = true;
      retry.classList.remove("hidden");
      _ventaPickerMsg("No pudimos leer tus campañas del CRM.", true);
    });
}

// Chip con el saldo de la campaña elegida: en rojo si está en cero, que es el
// caso en el que el envío se va a rebotar.
function _mostrarSaldoVenta(idventa) {
  const chip = document.getElementById("venta-picker-saldo");
  if (!chip) return;
  if (!idventa || !(String(idventa) in _ventasSaldo)) {
    chip.classList.add("hidden");
    return;
  }
  const disp = _ventasSaldo[String(idventa)];
  chip.textContent = `Disponible $${disp.toFixed(2)}`;
  chip.classList.toggle("venta-picker-saldo--bajo", disp <= 0);
  chip.classList.remove("hidden");
}

function onVentaElegida() {
  window._ventaElegida = document.getElementById("venta-picker-select").value || "";
  _mostrarSaldoVenta(window._ventaElegida);
  if (window._ventaElegida) {
    document.getElementById("venta-picker").classList.remove("venta-picker--falta");
    const disp = _ventasSaldo[String(window._ventaElegida)];
    _ventaPickerMsg(disp <= 0 ? "Esta campaña no tiene saldo disponible: el envío va a fallar." : "");
  } else {
    _ventaPickerMsg("");
  }
}

// Tipo de producto actualmente elegido (likes/views/shares/reposts/saves/reach) o null.
function _tipoActual() {
  const prodSelect = document.getElementById("orden-producto");
  return _tipoProducto(prodSelect.options[prodSelect.selectedIndex]?.text || "");
}

// Cantidades ya enviadas a este cliente para ese tipo de producto. Se cachean por
// (cliente, tipo) y se suman en memoria las que se van agregando en esta sesión,
// para no repetir ni siquiera antes de que la orden llegue al CRM.
const _usadasCache = {};
async function _cantidadesUsadas(tipo) {
  const ig = window._clientIg || "";
  if (!ig || !tipo) return new Set();
  const key = `${ig}|${tipo}`;
  if (!_usadasCache[key]) {
    _usadasCache[key] = new Set();
    try {
      const r = await fetch(`/api/cantidades_usadas?client=${encodeURIComponent(ig)}&tipo=${encodeURIComponent(tipo)}`);
      const d = await r.json();
      (d.usadas || []).forEach((v) => _usadasCache[key].add(Number(v)));
    } catch { /* si falla, seguimos sin historial */ }
  }
  return _usadasCache[key];
}

function _marcarUsada(tipo, val) {
  const ig = window._clientIg || "";
  if (!ig || !tipo) return;
  const key = `${ig}|${tipo}`;
  (_usadasCache[key] = _usadasCache[key] || new Set()).add(Number(val));
}

// Tira una cantidad al azar dentro del rango SIN repetir una ya enviada a este
// cliente (Facu: "que nunca repita la cantidad"). Si ya se usaron todas las del
// rango, avisa y permite repetir para no bloquear la operación.
// Tira un número del rango sin repetir uno ya enviado a este cliente. Devuelve
// {val, agotado}: agotado = ya se usaron todas las del rango y hubo que repetir.
async function _tirarDelRango(tipo, r) {
  const min = Math.min(r.min, r.max), max = Math.max(r.min, r.max);
  const usadas = await _cantidadesUsadas(tipo);
  const disponibles = [];
  for (let v = min; v <= max; v++) if (!usadas.has(v)) disponibles.push(v);
  const agotado = disponibles.length === 0;
  const val = agotado
    ? Math.floor(min + Math.random() * (max - min + 1))
    : disponibles[Math.floor(Math.random() * disponibles.length)];
  _marcarUsada(tipo, val);
  return { val, agotado, min, max };
}

// ── Piso de views: nunca menos de 5× los likes del mismo post ────────────────
// Un post con 1.000 likes y 300 views se ve falso. La regla es un MÍNIMO: si el
// azar del rango da menos, se sube; si da más, se respeta.
const VIEWS_POR_LIKE = 5;

function _totalDeTipo(tipo) {
  return ordenes
    .filter(o => _tipoProducto(o.productoNombre) === tipo)
    .reduce((a, o) => a + (parseInt(o.cantidad) || 0), 0);
}

// Mínimo de views del post según los likes ya cargados. Editar una orden la
// saca de la lista, así que no hay que descontarla acá.
function _pisoViewsActual() {
  return _totalDeTipo("likes") * VIEWS_POR_LIKE;
}

// Devuelve {val, subido}: subido = hubo que levantarlo para respetar el piso.
function _pisoViews(val) {
  const piso = _pisoViewsActual();
  return piso > val ? { val: piso, subido: true, piso } : { val, subido: false, piso };
}

async function rollCantidad() {
  const r = _rangoDelProducto();
  if (!r || r.min == null || r.max == null) return;
  const tipo = _tipoActual();
  const { val: tirado, agotado, min, max } = await _tirarDelRango(tipo, r);

  // En views el rango es el punto de partida, pero el piso de 5× los likes manda.
  let val = tirado, subidoPorLikes = false, piso = 0;
  if (tipo === "views") {
    const p = _pisoViews(tirado);
    val = p.val; subidoPorLikes = p.subido; piso = p.piso;
  }

  const hint = document.getElementById("orden-cantidad-hint");
  if (subidoPorLikes && hint) {
    hint.textContent = `Subido a ${val.toLocaleString("es-AR")}: los views van como mínimo ${VIEWS_POR_LIKE}× los likes del post (${piso.toLocaleString("es-AR")}).`;
    hint.classList.remove("hidden");
  } else if (agotado && hint) {
    hint.textContent = `Ya se usaron todas las cantidades entre ${min} y ${max} para este cliente; puede repetirse.`;
    hint.classList.remove("hidden");
  }

  const el = document.getElementById("orden-cantidad");
  el.value = val;
  clearFieldError("orden-cantidad");
  obtenerCosto();
}

// ── Órdenes precreadas a partir de los rangos ────────────────────────────────
// Al entrar al paso de órdenes se arma sola una orden por cada producto con
// rango configurado, con cantidad al azar dentro del rango.
// Los rangos son los del cliente del post; si el post no es de ningún cliente,
// los del cliente genérico (los resuelve el backend en el meta del scrape).
// Son órdenes normales: se pueden editar o borrar antes de enviar.
// Producto exacto elegido como calidad del cliente, si sigue estando.
function _productoElegido(grupos, prodId) {
  if (!prodId) return null;
  for (const g of grupos || [])
    for (const item of g.items || [])
      if (String(item.id) === String(prodId)) return item;
  return null;
}

function _productoDeTipo(grupos, tipo) {
  const candidatos = [];
  for (const g of grupos || []) {
    for (const item of g.items || []) {
      if (_tipoProducto(item.nombre) === tipo) candidatos.push(item);
    }
  }
  if (!candidatos.length) return null;
  // Preferimos el producto BASE ("Likes", "Views", "Shares", "Reposts"...) sobre las variantes
  // con proveedor o formato distinto ("Story Views", "Views Live", "Likes 1178
  // JAP"): el nombre antes del precio tiene que ser el tipo pelado.
  const base = candidatos.find(c =>
    (c.nombre.split("(")[0] || "").trim().toLowerCase() === tipo);
  return base || candidatos[0];
}

async function _costoDe(rsId, prodId, cantidad) {
  try {
    const r = await fetch("/api/costo_trafico", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redsocial: rsId, producto: prodId, cant_solicitada: cantidad }),
    });
    const d = await r.json();
    return d.costoTrafico != null ? parseFloat(d.costoTrafico) : null;
  } catch { return null; }
}

// "YYYY-MM-DD HH:MM", que es lo que espera el CRM en fechaProgramada.
function _fmtFechaCrm(d) {
  const p = x => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function _precrearOrdenesDeRangos() {
  const ranges = window._clientRanges || {};
  const rsId = document.getElementById("orden-redsocial").value;
  const grupos = _productosCache[rsId] || [];
  let oid = Date.now() + 1000;

  for (const tipo of RANGE_KEYS) {
    // Una orden por entrada: un cliente puede pedir dos calidades del mismo
    // producto en el mismo post, cada una con su rango.
    const entradas = _entradasDeRango(ranges[tipo]);
    for (let i = 0; i < entradas.length; i++) {
      const r = entradas[i];
      if (!r || r.min == null || r.max == null) continue;
      // Si ya hay una orden de esa entrada (precreada antes o cargada a mano),
      // no la duplicamos: volver al paso de órdenes no debe sumar de nuevo.
      const rangoKey = `${tipo}|${r.prod_id || "auto"}|${i}`;
      if (ordenes.some(o => o.rangoKey === rangoKey)) continue;
      // Calidad fijada para este cliente (variante concreta del CRM). Si ya no
      // existe en esta red social, caemos a la base en vez de no precrear nada.
      const prod = _productoElegido(grupos, r.prod_id) || _productoDeTipo(grupos, tipo);
      if (!prod) continue;   // el CRM no ofrece ese producto para esta red

      let { val } = await _tirarDelRango(tipo, r);
      // Los likes se precrean primero (van antes en RANGE_KEYS), así que acá ya
      // están en la lista y se puede atar el piso de views a ellos.
      if (tipo === "views") val = _pisoViews(val).val;

      // División configurada en la ficha del cliente: el total se parte en N
      // tandas espaciadas (una orden programada por tanda) para que el post no
      // reciba 3.000 de golpe y parezca comprado.
      const n = [3, 5].includes(Number(r.split)) ? Number(r.split) : 1;
      const cadaMin = Number(r.cada_min) || 120;
      const partes = n > 1 ? calcularSplitPartes(val, n) : [val];
      const costoTotal = await _costoDe(rsId, prod.id, val);
      const ahora = Date.now();

      for (let t = 0; t < partes.length; t++) {
        const cuandoFecha = new Date(ahora + t * cadaMin * 60 * 1000);
        const programada = n > 1 && t > 0;   // la primera tanda sale ya
        ordenes.push({
          id: oid++,
          redsocial: _currentNombreRed || "Instagram",
          redsocialId: rsId,
          productoId: prod.id,
          productoNombre: prod.nombre,
          cantidad: partes[t],
          link: currentUrl,
          cuando: programada ? "programar" : "ahora",
          cuandoLabel: programada
            ? cuandoFecha.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" })
            : "Ahora",
          fechaProgramada: programada ? _fmtFechaCrm(cuandoFecha) : "",
          obs: "",
          tipo: "normal",
          rangoKey,          // marca de precreada, para no duplicar
          ...(n > 1 ? { splitIndex: t + 1, splitTotal: n } : {}),
          costo: costoTotal != null ? costoTotal * (partes[t] / val) : null,
        });
      }
    }
  }
}

function onCantidadInput() {
  clearFieldError("orden-cantidad");
  obtenerCosto();
}

// ── Custom RS dropdown ───────────────────────────────────────────────────────
function toggleRsDropdown(e) {
  e.stopPropagation();
  document.getElementById("rs-dropdown").classList.toggle("hidden");
}

function selectRs(el) {
  const value = el.dataset.value;
  const label = el.dataset.label;
  const slug  = el.dataset.slug;
  const color = el.dataset.color;

  document.getElementById("orden-redsocial").value = value;
  document.getElementById("rs-selected-label").textContent = label;

  const iconEl = document.getElementById("rs-selected-icon");
  if (slug) {
    iconEl.src = `https://cdn.simpleicons.org/${slug}/${color}`;
    iconEl.style.display = "";
  } else {
    iconEl.style.display = "none";
  }

  document.getElementById("rs-dropdown").classList.add("hidden");

  // Mark active
  document.querySelectorAll(".rs-option").forEach(o => o.classList.remove("rs-option--active"));
  el.classList.add("rs-option--active");

  actualizarProductos();
}

document.addEventListener("click", () => {
  const dd = document.getElementById("rs-dropdown");
  if (dd) dd.classList.add("hidden");
});

function selectCuando(btn) {
  const val = btn.dataset.value;

  if (val === "split3" || val === "split5") {
    abrirIntervaloModal(val, btn);
    return;
  }

  document.querySelectorAll(".cuando-pill").forEach(b => b.classList.remove("cuando-pill--active"));
  btn.classList.add("cuando-pill--active");
  document.getElementById("orden-cuando-val").value = val;
  document.getElementById("orden-fecha-wrap").classList.toggle("hidden", val !== "programar");
}

let _intervaloPendingBtn = null;

function calcularSplitPartes(cantidad, n) {
  const base = Math.floor(cantidad / n);
  const resto = cantidad - base * n;
  const partes = [];
  for (let i = 0; i < n; i++) partes.push(base + (i < resto ? 1 : 0));
  return partes;
}

function abrirIntervaloModal(val, btn) {
  _intervaloPendingBtn = { val, btn };
  document.getElementById("intervalo-input-error").classList.add("hidden");
  document.getElementById("intervalo-input").classList.remove("orden-input--error");
  selectIntervaloUnidad(document.getElementById("orden-intervalo-unidad").value || "minutos");
  document.getElementById("intervalo-input").value = document.getElementById("orden-intervalo-valor").value || "60";
  show("intervalo-overlay");
}

function cerrarIntervaloModal() {
  _intervaloPendingBtn = null;
  hide("intervalo-overlay");
}

function selectIntervaloUnidad(unidad) {
  document.getElementById("intervalo-unidad-minutos").classList.toggle("cuando-pill--active", unidad === "minutos");
  document.getElementById("intervalo-unidad-dias").classList.toggle("cuando-pill--active", unidad === "dias");
  document.getElementById("intervalo-hint").textContent = unidad === "dias"
    ? "Ingresá el intervalo en días (mínimo 1)."
    : "Ingresá el intervalo en minutos (mínimo 45).";
  document.getElementById("intervalo-input").dataset.unidad = unidad;
  document.getElementById("intervalo-input-error").classList.add("hidden");
  document.getElementById("intervalo-input").classList.remove("orden-input--error");
}

function confirmarIntervalo() {
  if (!_intervaloPendingBtn) return;
  const { val, btn } = _intervaloPendingBtn;

  const unidad = document.getElementById("intervalo-input").dataset.unidad || "minutos";
  const inputEl = document.getElementById("intervalo-input");
  const valor = parseInt(inputEl.value);
  const errorEl = document.getElementById("intervalo-input-error");
  const min = unidad === "dias" ? 1 : 45;

  if (!valor || valor < min) {
    inputEl.classList.add("orden-input--error");
    errorEl.textContent = unidad === "dias"
      ? "Ingresá el intervalo en días (mínimo 1)."
      : "Ingresá el intervalo en minutos (mínimo 45).";
    errorEl.classList.remove("hidden");
    return;
  }

  const n = val === "split3" ? 3 : 5;
  const cantidad = parseInt(document.getElementById("orden-cantidad").value) || 0;
  const cantMin = _lastCantMin || 0;
  if (cantidad > 0 && cantMin > 0) {
    const partes = calcularSplitPartes(cantidad, n);
    if (Math.min(...partes) < cantMin) {
      inputEl.classList.remove("orden-input--error");
      errorEl.textContent = `Cantidad inválida. Pediste ${cantidad} en ${n} órdenes (${partes.join(" + ")}), pero el mínimo por orden es ${cantMin}.`;
      errorEl.classList.remove("hidden");
      return;
    }
  }

  document.getElementById("orden-intervalo-unidad").value = unidad;
  document.getElementById("orden-intervalo-valor").value = valor;

  document.querySelectorAll(".cuando-pill").forEach(b => b.classList.remove("cuando-pill--active"));
  btn.classList.add("cuando-pill--active");
  document.getElementById("orden-cuando-val").value = val;
  document.getElementById("orden-fecha-wrap").classList.add("hidden");

  cerrarIntervaloModal();
}

function onCuandoChange() {
  const val = document.querySelector('input[name="orden_cuando"]:checked')?.value;
  const fechaWrap = document.getElementById("orden-fecha-wrap");
  fechaWrap.classList.toggle("hidden", val !== "programar");
}

function setFieldError(fieldId, msg) {
  const input = document.getElementById(fieldId);
  const error = document.getElementById(fieldId + "-error");
  if (input) input.classList.add("orden-input--error");
  if (error) { error.textContent = msg; error.classList.remove("hidden"); }
}

function clearFieldError(fieldId) {
  const input = document.getElementById(fieldId);
  const error = document.getElementById(fieldId + "-error");
  if (input) input.classList.remove("orden-input--error");
  if (error) error.classList.add("hidden");
}

function agregarOrden() {
  const rsSelect = document.getElementById("orden-redsocial");
  const prodSelect = document.getElementById("orden-producto");
  const cantidadEl = document.getElementById("orden-cantidad");
  const linkEl = document.getElementById("orden-link");
  const obsEl = document.getElementById("orden-obs");
  const cuando = document.getElementById("orden-cuando-val")?.value || "ahora";
  const fechaEl = document.getElementById("orden-fecha");

  let valid = true;

  if (!prodSelect.value) {
    setFieldError("orden-producto", "Seleccioná un producto");
    valid = false;
  } else clearFieldError("orden-producto");

  const cantidad = parseInt(cantidadEl.value);
  const tipoOrden = _tipoProducto(prodSelect.options[prodSelect.selectedIndex]?.text || "");
  const pisoViews = _pisoViewsActual();
  if (!cantidad || cantidad < 1) {
    setFieldError("orden-cantidad", "Ingresá una cantidad válida");
    valid = false;
  } else if (tipoOrden === "views" && cantidad < pisoViews) {
    // Un post con muchos likes y pocas views se ve comprado: el piso es duro.
    setFieldError("orden-cantidad",
      `Mínimo ${pisoViews.toLocaleString("es-AR")}: los views van al menos ${VIEWS_POR_LIKE}× los likes del post`);
    valid = false;
  } else clearFieldError("orden-cantidad");

  const link = linkEl.value.trim();
  if (!link) {
    setFieldError("orden-link", "Ingresá el link");
    valid = false;
  } else clearFieldError("orden-link");

  if (cuando === "programar" && !fechaEl.value) {
    fechaEl.classList.add("orden-input--error");
    valid = false;
  } else fechaEl.classList.remove("orden-input--error");

  if (!valid) return;

  const isSplit = cuando === "split3" || cuando === "split5";
  const base = {
    redsocial: document.getElementById("rs-selected-label")?.textContent || "Instagram",
    redsocialId: rsSelect.value,
    productoId: parseInt(prodSelect.value),
    productoNombre: prodSelect.selectedIndex >= 0 ? prodSelect.options[prodSelect.selectedIndex].text : "",
    link,
    obs: obsEl.value.trim(),
    tipo: "normal",
  };

  if (isSplit) {
    const n = cuando === "split3" ? 3 : 5;
    const unidad = document.getElementById("orden-intervalo-unidad").value || "minutos";
    const valor = parseInt(document.getElementById("orden-intervalo-valor").value) || 0;
    const stepMs = (unidad === "dias" ? valor * 24 * 60 * 60 : valor * 60) * 1000;
    const pad = x => String(x).padStart(2, "0");
    const now = Date.now();
    const partes = calcularSplitPartes(cantidad, n);

    for (let i = 0; i < n; i++) {
      const d = new Date(now + i * stepMs);
      const fechaProgramada = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
      ordenes.push({
        ...base,
        id: Date.now() + i,
        cantidad: partes[i],
        costo: _lastCosto ? _lastCosto * (partes[i] / cantidad) : _lastCosto,
        cuando: "programar",
        cuandoLabel: d.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" }),
        fechaProgramada,
        splitIndex: i + 1,
        splitTotal: n,
      });
    }
  } else {
    const cuandoLabel = {
      ahora: "Ahora",
      programar: fechaEl.value ? new Date(fechaEl.value).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" }) : "Programado",
    }[cuando] || "Ahora";

    ordenes.push({
      ...base,
      id: Date.now(),
      cantidad,
      costo: _lastCosto,
      cuando,
      cuandoLabel,
      fechaProgramada: cuando === "programar" && fechaEl.value
        ? (() => {
            const d = new Date(fechaEl.value);
            return isNaN(d) ? "" : `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
          })()
        : "",
    });
  }

  // Reset form
  prodSelect.value = "";
  cantidadEl.value = "";
  obsEl.value = "";
  fechaEl.value = "";
  document.getElementById("orden-cuando-val").value = "ahora";
  document.querySelectorAll(".cuando-pill").forEach(b => b.classList.remove("cuando-pill--active"));
  const pillAhora = document.querySelector('.cuando-pill[data-value="ahora"]');
  if (pillAhora) pillAhora.classList.add("cuando-pill--active");
  document.getElementById("orden-fecha-wrap").classList.add("hidden");
  document.getElementById("opt-split3").style.display = "none";
  document.getElementById("opt-split5").style.display = "none";
  document.getElementById("orden-demora-badge")?.classList.add("hidden");
  document.getElementById("orden-costo-badge")?.classList.add("hidden");
  document.getElementById("orden-cantidad-hint").classList.add("hidden");
  const costoField = document.getElementById("orden-costo-display");
  if (costoField) costoField.value = "";
  _lastCosto = null;

  renderOrdenes();

  // Flash feedback on card list
  const acumCard = document.getElementById("ordenes-acumuladas-card");
  acumCard.classList.add("orden-card-flash");
  setTimeout(() => acumCard.classList.remove("orden-card-flash"), 400);
}

function eliminarOrden(id) {
  ordenes = ordenes.filter(o => o.id !== id);
  renderOrdenes();
}

function editarOrden(id) {
  const o = ordenes.find(ord => ord.id === id);
  if (!o) return;

  // Para órdenes de comentarios: volver al step de comentarios para reseleccionar
  if (o.tipo === "comentarios") {
    ordenes = ordenes.filter(ord => ord.id !== id);
    renderOrdenes();
    hide("step-ordenes");
    show("step-comentarios");
    document.getElementById("panel-seleccionados").classList.remove("hidden");
    document.getElementById("comments-counter").classList.remove("hidden");
    return;
  }

  // Cargar red social en el custom dropdown
  const rsOption = document.querySelector(`.rs-option[data-value="${o.redsocialId}"]`);
  if (rsOption) selectRs(rsOption);

  // Cargar producto (esperar a que actualizarProductos cargue y luego setear)
  const prodSelect = document.getElementById("orden-producto");
  const afterLoad = () => {
    prodSelect.value = o.productoId;
    onProductoChange();
  };
  // Si ya está cargado ese producto en el select, setear directo
  if ([...prodSelect.options].some(opt => opt.value == o.productoId)) {
    afterLoad();
  } else {
    // esperar a que actualizarProductos termine
    const orig = window._onProductosLoaded;
    window._onProductosLoaded = () => { afterLoad(); window._onProductosLoaded = orig; };
  }

  // Cantidad, link, obs
  document.getElementById("orden-cantidad").value = o.cantidad;
  document.getElementById("orden-link").value = o.link;
  document.getElementById("orden-obs").value = o.obs || "";

  // Cuándo
  document.getElementById("orden-cuando-val").value = o.cuando;
  document.querySelectorAll(".cuando-pill").forEach(b => b.classList.remove("cuando-pill--active"));
  const pill = document.querySelector(`.cuando-pill[data-value="${o.cuando}"]`);
  if (pill) pill.classList.add("cuando-pill--active");
  const fechaWrap = document.getElementById("orden-fecha-wrap");
  if (o.cuando === "programar") {
    fechaWrap.classList.remove("hidden");
  } else {
    fechaWrap.classList.add("hidden");
  }

  // Eliminar la orden (se re-agrega al guardar)
  ordenes = ordenes.filter(ord => ord.id !== id);
  renderOrdenes();

  // Scroll al form
  document.querySelector(".orden-form-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderOrdenes() {
  const lista = document.getElementById("ordenes-lista");
  const acumCard = document.getElementById("ordenes-acumuladas-card");
  const totalBadge = document.getElementById("ordenes-total-badge");
  const countBadge = document.getElementById("ordenes-count-badge");
  const btnSolicitar = document.getElementById("btn-solicitar");

  totalBadge.textContent = ordenes.length;
  countBadge.textContent = ordenes.length;
  btnSolicitar.disabled = ordenes.length === 0;
  acumCard.style.display = ordenes.length === 0 ? "none" : "";

  // Total cost
  const totalCostoEl = document.getElementById("ordenes-total-costo");
  if (totalCostoEl) {
    const totalCosto = ordenes.reduce((sum, o) => sum + (o.costo || 0), 0);
    if (totalCosto > 0) {
      totalCostoEl.textContent = `Total estimado: $${totalCosto.toFixed(4)}`;
      totalCostoEl.classList.remove("hidden");
    } else {
      totalCostoEl.classList.add("hidden");
    }
  }

  const CUANDO_ICONS = { ahora: "⚡", programar: "🗓", split3: "÷3", split5: "÷5" };

  // Los likes se pueden cargar DESPUÉS de los views y dejar el post corto: el
  // piso no se puede reescribir solo sin pisar lo que cargó el vendedor, así
  // que se avisa arriba de la lista.
  const piso = _pisoViewsActual();
  const views = _totalDeTipo("views");
  const avisoViews = (views > 0 && views < piso)
    ? `<div class="orden-aviso">⚠ Los views quedaron en ${views.toLocaleString("es-AR")} y con estos likes tendrían que ser al menos ${piso.toLocaleString("es-AR")} (${VIEWS_POR_LIKE}× los likes). Editá la orden de views antes de enviar.</div>`
    : "";

  lista.innerHTML = avisoViews + ordenes.map((o, i) => {
    const rs = RS_META[o.redsocialId] || { icon: "🌐", label: o.redsocial, color: "#a0a0a0", slug: "" };
    const rsIconHtml = rs.slug
      ? `<img src="https://cdn.simpleicons.org/${rs.slug}/${rs.color.replace('#','')}" class="rs-card-icon" />`
      : `<span>${rs.icon}</span>`;
    return `
    <div class="orden-card">
      <div class="orden-card-accent" style="background:${rs.color};"></div>
      <div class="orden-card-content">
        <div class="orden-card-header">
          <span class="orden-card-num">${i + 1}</span>
          <span class="orden-card-rs" style="color:${rs.color};background:${rs.color}18;border-color:${rs.color}33;">
            ${rsIconHtml} ${escapeHtml(rs.label)}
          </span>
          <span class="orden-card-nombre">${escapeHtml(o.productoNombre)}</span>
          ${o.splitTotal ? `<span class="orden-card-pill orden-card-pill--split">${o.splitIndex}/${o.splitTotal}</span>` : ""}
          ${o.turno ? `<span class="orden-card-pill orden-card-pill--split">⏰ turno ${o.turno}</span>` : ""}
        </div>
        <div class="orden-card-meta">
          <span class="orden-card-pill orden-card-pill--qty">${o.tipo === "comentarios" ? `${o.cantidad} comentarios` : `${o.cantidad.toLocaleString()} uds`}</span>
          <span class="orden-card-pill orden-card-pill--when">${(o.splitTotal || o.turno) ? "⏰" : (CUANDO_ICONS[o.cuando] || "⚡")} ${escapeHtml(o.cuandoLabel)}</span>
          ${o.costo != null && o.costo > 0 ? `<span class="orden-card-pill orden-card-pill--cost">$${parseFloat(o.costo).toFixed(4)}</span>` : ""}
          ${o.tipo === "comentarios" ? `<span class="orden-card-pill orden-card-pill--green">✓ Comentarios IA</span>` : ""}
          ${o.rangoKey ? `<span class="orden-card-pill orden-card-pill--auto" title="Cantidad al azar dentro del rango configurado">🎲 Automática</span>` : ""}
        </div>
        <div class="orden-card-link">${escapeHtml(o.link)}</div>
        ${o.obs ? `<div class="orden-card-obs">"${escapeHtml(o.obs)}"</div>` : ""}
      </div>
      <div class="orden-card-remove">
        <button class="orden-card-btn-edit" onclick="editarOrden(${o.id})" title="Editar">✎</button>
        <button class="orden-card-btn-del" onclick="eliminarOrden(${o.id})" title="Eliminar">×</button>
      </div>
    </div>`;
  }).join("");
}

function volverAComentarios() {
  hide("step-ordenes");
  show("step-comentarios");
  document.getElementById("panel-seleccionados").classList.remove("hidden");
  document.getElementById("comments-counter").classList.remove("hidden");
}

// ── Pantalla de resultado ────────────────────────────────────────────────────
// Antes se imprimía el informe crudo del CRM (un bloque de texto con códigos
// tipo BUCKET_COM_INMED repetidos). Ahora ese texto queda como "detalle
// técnico" y arriba se muestra qué pasó realmente con cada orden.

// Traduce un mensaje del CRM a algo legible. Devuelve {icono, texto, cod}.
function _leerMensajeCRM(m) {
  const txt = String(m || "").trim();
  const cod = (txt.match(/\(c[óo]d\.?\s*([A-Z0-9_]+)\)/) || [])[1] || "";
  // Se saca el código y el dominio: no aportan nada al vendedor.
  let limpio = txt.replace(/\(c[óo]d\.?\s*[A-Z0-9_]+\)/, "")
                  .replace(/\((?:www\.)?[a-z0-9.-]+\.[a-z]{2,}\)/gi, "")
                  .replace(/^[ℹ️✅⚠️❌\s]+/, "")
                  .replace(/\s{2,}/g, " ")
                  .trim();
  const fecha = (limpio.match(/(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)/) || [])[1];
  const esProg = /programad/i.test(limpio) || /_PROG/.test(cod);
  const esError = /^❌/.test(txt) || /error|rechaz|falló|fallo/i.test(limpio);
  const esOk = /^✅/.test(txt) || /registramos/i.test(limpio);

  const tipo = /coment/i.test(limpio) ? "Comentarios"
             : /tr[áa]fico/i.test(limpio) ? "Tráfico" : "";
  if (esError) return { icono: "❌", clase: "bad", texto: limpio, cod };
  // "Registramos N ordenes" ya está en el contador del bloque: no se repite.
  if (esOk)    return { icono: "✅", clase: "ok", texto: "", cod };
  if (esProg)  return { icono: "⏰", clase: "prog",
                        texto: `${tipo || "Orden"}: programada${fecha ? " para el " + fecha.replace(" ", " a las ") : ""}`, cod };
  if (/inmediat/i.test(limpio))
    return { icono: "⚡", clase: "now", texto: `${tipo || "Orden"}: envío inmediato`, cod };
  return { icono: "ℹ️", clase: "info", texto: limpio, cod };
}

// Los mensajes vienen repetidos (uno por orden). Se agrupan y se cuentan.
function _agruparMensajes(msgs) {
  const out = [];
  for (const m of msgs || []) {
    const l = _leerMensajeCRM(m);
    if (!l.texto) continue;
    const prev = out.find(x => x.texto === l.texto && x.cod === l.cod);
    if (prev) prev.n++; else out.push({ ...l, n: 1 });
  }
  return out;
}

function _bloqueMensajes(titulo, insertadas, msgs) {
  const items = _agruparMensajes(msgs);
  if (!items.length && !insertadas) return "";
  return `
    <div class="res-bloque">
      <div class="res-bloque-t">${escapeHtml(titulo)}
        <span class="res-chip">${insertadas} ${insertadas === 1 ? "orden" : "órdenes"}</span>
      </div>
      ${items.map(i => `
        <div class="res-msg res-msg--${i.clase}">
          <span class="res-msg-ico">${i.icono}</span>
          <span class="res-msg-txt">${escapeHtml(i.texto)}</span>
          ${i.n > 1 ? `<span class="res-msg-n">×${i.n}</span>` : ""}
        </div>`).join("")}
    </div>`;
}

function renderResultado(data, nComentarios, nTrafico) {
  const box = document.getElementById("resultado-content");
  const rc = data.resultado || null;                 // órdenes de comentarios
  const rt = data.trafico || null;                   // órdenes de tráfico
  const errores = [
    ...(data.error ? [data.error] : []),
    ...((rc && rc.errors) || []),
  ];
  const insCom = rc ? (rc.insertadas || 0) : 0;
  const insTra = rt ? (rt.insertadas || 0) : 0;
  // Los encabezados hombres:/mujeres: no son comentarios: no se cuentan ni se listan.
  const publicados = comentariosParaPublicar.filter(c => !generoDeHeader(c));

  const enviadas = nComentarios + nTrafico;
  const insertadas = insCom + insTra;
  const gastado = ordenes.reduce((s, o) => s + (o.costo || 0), 0);
  // Programadas: las que no salen ya. Es lo primero que pregunta el vendedor.
  const programadas = ordenes.filter(o => o.cuando !== "ahora").length;

  const fallo = errores.length > 0;
  // Que el CRM acepte MENOS órdenes de las que mandamos es el caso peligroso:
  // antes se perdía entre los mensajes y el vendedor creía que salió todo.
  const faltan = !fallo && insertadas > 0 && insertadas < enviadas;
  const estado = fallo ? { clase: "bad", ico: "✕", txt: "No se pudo enviar" }
    : faltan ? { clase: "warn", ico: "!", txt: `Se enviaron ${insertadas} de ${enviadas}` }
    : { clase: "ok", ico: "✓", txt: "Enviado correctamente" };

  box.innerHTML = `
    <div class="res-head">
      <span class="res-estado res-estado--${estado.clase}">${estado.ico} ${escapeHtml(estado.txt)}</span>
      <span class="res-head-r">
        <span class="res-fecha">${new Date().toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" })}</span>
        <button class="res-copy" onclick="copiarInforme(this)">Copiar informe</button>
      </span>
    </div>

    ${currentUrl ? `<a class="res-post" href="${escapeHtml(currentUrl)}" target="_blank" rel="noopener">
      ↗ Ver el post en Instagram</a>` : ""}

    <div class="res-stats">
      <div class="res-stat"><b>${insertadas}</b><span>órdenes en el CRM</span></div>
      ${nComentarios ? `<div class="res-stat"><b>${publicados.length}</b><span>comentarios publicados</span></div>` : ""}
      ${programadas ? `<div class="res-stat"><b>${programadas}</b><span>quedan programadas</span></div>` : ""}
      ${gastado > 0 ? `<div class="res-stat"><b>$${gastado.toFixed(2)}</b><span>costo de la campaña</span></div>` : ""}
    </div>

    ${errores.length ? `<div class="res-bloque res-bloque--err">
      <div class="res-bloque-t">No se pudo completar</div>
      ${errores.map(e => `<div class="res-msg res-msg--bad"><span class="res-msg-ico">❌</span><span class="res-msg-txt">${escapeHtml(e)}</span></div>`).join("")}
    </div>` : ""}

    ${faltan ? `<div class="res-bloque res-bloque--err">
      <div class="res-bloque-t">Revisá en el CRM</div>
      <div class="res-msg res-msg--bad"><span class="res-msg-ico">⚠️</span><span class="res-msg-txt">
        Mandamos ${enviadas} órdenes y el CRM registró ${insertadas}. Fijate cuál falta antes de rearmarla.
      </span></div>
    </div>` : ""}

    ${_bloqueOrdenes()}

    ${nComentarios ? _bloqueMensajes("Confirmación del CRM · comentarios", insCom, (rc && rc.messages) || []) : ""}
    ${nTrafico ? _bloqueMensajes("Confirmación del CRM · tráfico", insTra, [...((rt && rt.messages) || []), ...((rt && rt.warnings) || [])]) : ""}

    ${data.informe ? `<details class="res-detalle">
      <summary>Ver informe técnico</summary>
      <pre id="res-informe-raw">${escapeHtml(data.informe)}</pre>
    </details>` : ""}
  `;

  const card = document.getElementById("resultado-comments-card");
  if (nComentarios && publicados.length) {
    card.classList.remove("hidden");
    const t = card.querySelector(".card-title");
    if (t) t.textContent = `Comentarios publicados (${publicados.length})`;
    document.getElementById("resultado-comments").innerHTML = publicados.map((c, i) =>
      `<div class="resultado-comment-item"><span class="resultado-comment-num">${i + 1}</span><span class="resultado-comment-texto">${escapeHtml(c)}</span></div>`
    ).join("");
  } else if (card) {
    card.classList.add("hidden");
  }
}

// Qué se mandó, en los términos del vendedor (producto, cantidad, cuándo), no
// en los del CRM. Es la parte que de verdad se lee.
function _bloqueOrdenes() {
  if (!ordenes.length) return "";
  const filas = ordenes.map(o => {
    const rs = (typeof RS_META !== "undefined" && RS_META[o.redsocialId]) || { icon: "🌐", label: o.redsocial || "", color: "#a0a0a0" };
    const prog = o.cuando !== "ahora";
    const cant = o.tipo === "comentarios"
      ? `${o.cantidad} comentarios`
      : `${Number(o.cantidad || 0).toLocaleString("es-AR")} uds`;
    return `
      <div class="res-orden">
        <span class="res-orden-dot" style="background:${rs.color}"></span>
        <span class="res-orden-prod">${escapeHtml(o.productoNombre || "")}</span>
        <span class="res-orden-cant">${escapeHtml(cant)}</span>
        <span class="res-orden-when res-orden-when--${prog ? "prog" : "now"}">
          ${prog ? "⏰" : "⚡"} ${escapeHtml(o.cuandoLabel || (prog ? "programada" : "ahora"))}
        </span>
        ${o.costo > 0 ? `<span class="res-orden-costo">$${parseFloat(o.costo).toFixed(4)}</span>` : ""}
      </div>`;
  }).join("");
  return `<div class="res-bloque">
    <div class="res-bloque-t">Qué se mandó <span class="res-chip">${ordenes.length} ${ordenes.length === 1 ? "orden" : "órdenes"}</span></div>
    ${filas}
  </div>`;
}

// Copia el informe crudo: sirve para pegarlo en el grupo o pasárselo al cliente.
function copiarInforme(btn) {
  const pre = document.getElementById("res-informe-raw");
  const txt = pre ? pre.textContent : document.getElementById("resultado-content").innerText;
  navigator.clipboard?.writeText(txt).then(() => {
    const antes = btn.textContent;
    btn.textContent = "✓ Copiado";
    setTimeout(() => { btn.textContent = antes; }, 1600);
  }).catch(() => {});
}

async function solicitarOrdenes() {
  if (ordenes.length === 0) return;

  // Post sin cliente: no mandamos nada hasta saber de qué campaña se descuenta.
  // Solo aplica si hay órdenes de tráfico (los comentarios no tocan el saldo).
  const hayTrafico = ordenes.some(o => o.tipo !== "comentarios");
  if (hayTrafico && !window._clienteAsignado && !window._ventaElegida) {
    const box = document.getElementById("venta-picker");
    box.classList.remove("hidden");
    box.classList.add("venta-picker--falta");
    _ventaPickerMsg("Elegí una campaña para poder enviar el tráfico.", true);
    box.scrollIntoView({ behavior: "smooth", block: "center" });
    document.getElementById("venta-picker-select").focus();
    return;
  }

  const btn = document.getElementById("btn-solicitar");
  btn.disabled = true;
  btn.textContent = "Solicitando...";

  const ordenesComentarios = ordenes.filter(o => o.tipo === "comentarios");
  const ordenesNormales    = ordenes.filter(o => o.tipo !== "comentarios");

  try {
    let data = {};

    // Comentarios: publicar en Instagram vía IA. Puede haber 2 órdenes
    // (verificados 94 + no verificados 95), cada una con su propia lista.
    if (ordenesComentarios.length > 0 && comentariosParaPublicar.length > 0) {
      const resp = await fetch("/api/publicar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: currentUrl,
          // top-level: unión de todas (para el informe / fallback)
          comentarios: comentariosParaPublicar,
          ordenes: ordenesComentarios.map(o => ({
            ...o,
            // cantidad = comentarios reales (sin los encabezados hombres:/mujeres:)
            cantidad: (o.comentarios || []).filter(c => !generoDeHeader(c)).length,
          })),
        }),
      });
      data = await resp.json();
    }

    // Followers / likes / etc: enviar al CRM
    if (ordenesNormales.length > 0) {
      const costoTotal = ordenesNormales.reduce((s, o) => s + (o.costo || 0), 0);

      // Hora AR del servidor para programadas
      let serverDateAR = "";
      try {
        const tsResp = await fetch("/api/server_time_ar");
        const tsData = await tsResp.json();
        serverDateAR = tsData.ymdhmAR || "";  // "2026-06-25 06:54"
      } catch { /* silencioso */ }

      const crmOrdenes = ordenesNormales.map(o => {
        const programado = o.cuando !== "ahora" ? 1 : 0;
        const fechaProg  = o.cuando === "programar" && o.fechaProgramada
          ? o.fechaProgramada
          : (programado ? serverDateAR : null);
        return {
          redsocial_id: o.redsocialId,
          redsocial:    o.redsocial,
          prod:         o.productoNombre,
          demora:       " - ",
          url:          o.link,
          costo:        o.costo || 0,
          obs:          o.obs || "",
          cant_inicial: String(o.cantidad),
          cantidad:     String(o.cantidad),
          programado,
          fecha_programada: fechaProg || null,
          comentarios: [],
        };
      });

      const traficoResp = await fetch("/api/enviar_trafico", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // client/url viajan para que el registro de uso quede atado al cliente
        // (y así la tirada automática sepa qué cantidades ya se le enviaron).
        body: JSON.stringify({
          ordenes: crmOrdenes,
          costo_total: costoTotal,
          client: window._clientIg || "",
          // Solo va cuando el post no es de un cliente: el backend la valida
          // contra las campañas de la cuenta antes de usarla.
          idventa: window._ventaElegida || "",
          url: currentUrl,
        }),
      });
      const traficoData = await traficoResp.json().catch(() => ({}));
      if (traficoData.error || traficoData.errors?.length) {
        const err = traficoData.error || traficoData.errors.join(" | ");
        data.error = (data.error ? data.error + " | " : "") + err;
      } else {
        const lineas = [
          `Órdenes de tráfico insertadas: ${traficoData.insertadas ?? 0}`,
          ...(traficoData.messages || []),
          ...(traficoData.warnings || []),
        ];
        data.informe = (data.informe ? data.informe + "\n\n" : "") + lineas.join("\n");
      }
      data.trafico = traficoData;
    }

    hide("step-ordenes");
    renderResultado(data, ordenesComentarios.length, ordenesNormales.length);
    show("step-resultado");
  } catch (e) {
    // Sin alert() nativo: congela la pestaña. Mostramos el error en pantalla.
    hide("step-ordenes");
    const box = document.getElementById("resultado-content");
    box.innerHTML = `<span style="color:#e55;">Error al solicitar: ${escapeHtml(e.message || String(e))}</span>`;
    show("step-resultado");
    btn.disabled = false;
    btn.textContent = "Solicitar";
  }
}

function reiniciar() {
  currentUrl = "";
  comentariosGenerados = [];
  currentJobId = null;
  streamOffset = 0;
  streamProgresoOffset = 0;
  streamMeta = {};
  esperandoTranscripcion = false;
  pendingComentarios = [];
  document.getElementById("ig-link").value = "";
  refrescarBotonLimpiar();
  validarLinkVivo();
  pintarRecientes();
  currentKeyword = "";
  mostrarKeyword(false);
  keywordSugerida = "";
  keywordSugeridaPara = "";
  const hintKw = document.getElementById("ig-keyword-hint");
  if (hintKw) {
    hintKw.textContent = "Todos los comentarios van a ser esa palabra, alternando mayúsculas y minúsculas.";
    hintKw.classList.remove("ig-keyword-hint--detectada");
  }
  document.getElementById("lista-comentarios").innerHTML = "";
  document.getElementById("status-listo").classList.add("hidden");
  document.getElementById("scrape-owner").textContent = "—";
  document.getElementById("client-badge").textContent = "—";
  // Nada del post anterior sobrevive: ni el cliente ni la campaña elegida.
  window._clientIg = "";
  window._clienteAsignado = false;
  window._ventaElegida = "";
  document.getElementById("venta-picker")?.classList.add("hidden");
  document.getElementById("scrape-caption-block").classList.add("hidden");
  document.getElementById("photo-description-block").classList.add("hidden");
  document.getElementById("transcription-block").classList.add("hidden");
  document.getElementById("comments-actions-bar").classList.add("hidden");
  document.getElementById("stream-status").textContent = "";
  hide("loading-overlay");
  setError("");

  ordenes = [];
  comentariosParaPublicar = [];
  hide("step-loading");
  hide("step-comentarios");
  hide("step-ordenes");
  hide("step-resultado");
  show("step-input");
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function simularOrdenes() {
  currentUrl = "https://www.instagram.com/p/DZyYdR9xMLi/";
  comentariosParaPublicar = [
    "Qué bueno este contenido 🔥",
    "Me encanta lo que hacés!",
    "Tremendo post, sigue así 💪",
    "Top content como siempre",
    "Increíble, muy inspirador",
    "Siempre tan auténtico ❤️",
    "Esto es exactamente lo que necesitaba ver",
    "Wow, qué nivel de producción",
    "Me quedé sin palabras 🙌",
    "Sos una inspiración",
    "Cada post mejor que el anterior",
    "Compartiendo esto con todos mis amigos",
    "Genial como siempre 👏",
    "Así se hace, crack",
    "No puedo dejar de verlo",
    "Demasiado bueno esto",
    "Otro banger más 🔥🔥",
    "Me alegró el día",
    "Sigue así que vas para arriba",
    "10/10 sin dudas",
  ];
  ordenes = [];

  document.getElementById("ordenes-post-url").textContent = currentUrl;
  document.getElementById("ordenes-client").textContent = "Peter Fournier";
  document.getElementById("ordenes-hero-avatar").textContent = "P";
  document.getElementById("orden-link").value = currentUrl;

  ordenes.push({
    id: Date.now(),
    redsocial: "Instagram",
    redsocialId: "1",
    productoId: 94,
    productoNombre: "Comentarios Reales Verificados",
    cantidad: comentariosParaPublicar.length,
    link: currentUrl,
    cuando: "ahora",
    cuandoLabel: "Ahora",
    obs: "",
    tipo: "comentarios",
  });

  await actualizarProductos();
  renderOrdenes();
  prepararVentaPicker();

  hide("step-input");
  hide("step-comentarios");
  hide("step-resultado");
  document.getElementById("panel-seleccionados").classList.add("hidden");
  document.getElementById("comments-counter").classList.add("hidden");
  show("step-ordenes");
}

// ── Theme switcher ──
function toggleTheme() {
  const isLight = document.body.classList.toggle("light");
  localStorage.setItem("theme", isLight ? "light" : "dark");
  document.getElementById("btn-theme").textContent = isLight ? "☾ Dark" : "☀ Light";
}

// Aplicar tema guardado al cargar
(function() {
  const saved = localStorage.getItem("theme");
  if (saved === "light") {
    document.body.classList.add("light");
    document.addEventListener("DOMContentLoaded", () => {
      const btn = document.getElementById("btn-theme");
      if (btn) btn.textContent = "☾ Dark";
    });
  }
})();

// ── Home: pegar / limpiar el link ─────────────────────────────────────────

// Un link de post/reel de Instagram. Sirve para avisar ANTES de generar: un
// link de perfil o de otra red se scrapea igual y falla recién en el servidor,
// después de la espera.
const RE_POST_IG = /^https?:\/\/(www\.)?instagram\.com\/(p|reel|reels|tv)\/[A-Za-z0-9_-]+/i;

function estadoLink(url) {
  if (!url) return "";
  if (RE_POST_IG.test(url)) return "ok";
  return "warn";
}

// Feedback en el borde del campo + una línea de ayuda. No bloquea: si mañana
// cambia el formato de las URLs, el vendedor igual puede darle a generar.
function validarLinkVivo() {
  const url = document.getElementById("ig-link").value.trim();
  const field = document.getElementById("ig-link").closest(".ig-field");
  const hint = document.getElementById("ig-link-hint");
  const estado = estadoLink(url);

  field.classList.toggle("ig-field--ok", estado === "ok");
  field.classList.toggle("ig-field--warn", estado === "warn");

  if (!hint) return;
  if (estado === "warn") {
    hint.textContent = "Esto no parece un link de post o reel de Instagram.";
    hint.classList.remove("hidden");
  } else {
    hint.classList.add("hidden");
  }
}

// ── Links recientes ───────────────────────────────────────────────────────
// El mismo post se re-genera seguido (salió mal, el cliente pidió otro tono).
// Volver a buscarlo en Instagram y copiarlo de nuevo es el paso más molesto.
const RECIENTES_KEY = "growi_links_recientes";
const RECIENTES_MAX = 4;

function leerRecientes() {
  try {
    const raw = JSON.parse(localStorage.getItem(RECIENTES_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((u) => typeof u === "string") : [];
  } catch (e) {
    return [];
  }
}

function guardarLinkReciente(url) {
  try {
    const lista = [url, ...leerRecientes().filter((u) => u !== url)].slice(0, RECIENTES_MAX);
    localStorage.setItem(RECIENTES_KEY, JSON.stringify(lista));
  } catch (e) {
    /* localStorage lleno o bloqueado: los recientes son un extra */
  }
}

function etiquetaLink(url) {
  const m = url.match(/\/(p|reel|reels|tv)\/([A-Za-z0-9_-]+)/i);
  return m ? m[2].slice(0, 12) : url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 22);
}

function usarLinkReciente(url) {
  const input = document.getElementById("ig-link");
  input.value = url;
  input.dispatchEvent(new Event("input"));
  input.focus();
}

function pintarRecientes() {
  const wrap = document.getElementById("ig-recientes");
  if (!wrap) return;
  const lista = leerRecientes();
  wrap.innerHTML = "";
  wrap.classList.toggle("hidden", lista.length === 0);
  if (!lista.length) return;

  const titulo = document.createElement("span");
  titulo.className = "ig-recientes-label";
  titulo.textContent = "Recientes";
  wrap.appendChild(titulo);

  lista.forEach((url) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ig-reciente";
    chip.title = url;
    chip.textContent = etiquetaLink(url);
    chip.onclick = () => usarLinkReciente(url);
    wrap.appendChild(chip);
  });
}

function refrescarBotonLimpiar() {
  const btn = document.getElementById("ig-clear");
  if (!btn) return;
  const hay = document.getElementById("ig-link").value.trim() !== "";
  btn.classList.toggle("hidden", !hay);
}

function limpiarLink() {
  const input = document.getElementById("ig-link");
  input.value = "";
  refrescarBotonLimpiar();
  input.focus();
  input.dispatchEvent(new Event("input"));
}

// El navegador puede negar el portapapeles (permiso, http): en ese caso no
// rompemos nada, sólo dejamos el foco en el campo para pegar a mano.
async function pegarLink() {
  const input = document.getElementById("ig-link");
  try {
    const texto = (await navigator.clipboard.readText()).trim();
    if (texto) {
      input.value = texto;
      input.dispatchEvent(new Event("input"));
    }
  } catch (e) {
    /* sin permiso de portapapeles */
  }
  input.focus();
  refrescarBotonLimpiar();
}

// Enter key en el input
document.addEventListener("DOMContentLoaded", () => {
  const linkInput = document.getElementById("ig-link");
  linkInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") generarComentarios();
  });
  // Apenas hay un link, buscamos la palabra clave que pide el post. Se dispara
  // al pegar, al salir del campo y al escribir (con un respiro, para no pedirla
  // en cada tecla).
  let kwTimer = null;
  const pedirSugerencia = () => {
    clearTimeout(kwTimer);
    kwTimer = setTimeout(sugerirKeyword, 400);
  };
  linkInput.addEventListener("paste", () => setTimeout(pedirSugerencia, 0));
  linkInput.addEventListener("input", pedirSugerencia);
  linkInput.addEventListener("blur", sugerirKeyword);

  // La "x" del campo sólo tiene sentido cuando hay algo escrito.
  linkInput.addEventListener("input", () => {
    refrescarBotonLimpiar();
    validarLinkVivo();
  });
  refrescarBotonLimpiar();
  validarLinkVivo();
  pintarRecientes();
  linkInput.focus();

  // Atajos de la lista de comentarios: buscar y limpiar la búsqueda. Se enganchan
  // acá y no en el input para que funcionen con el foco en cualquier lado.
  document.addEventListener("keydown", (e) => {
    const filtro = document.getElementById("lista-filtro-input");
    const enLista = filtro && !document.getElementById("lista-filtro").classList.contains("hidden");
    if (!enLista) return;

    // Ctrl/Cmd+F: buscar dentro de la lista en vez del buscador del navegador,
    // que no sirve porque las filas ocultas por el filtro no están en el DOM
    // visible y el texto está repartido en decenas de nodos.
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
      e.preventDefault();
      filtro.focus();
      filtro.select();
      return;
    }
    // Escape con el filtro activo lo limpia (y no cierra nada más).
    if (e.key === "Escape" && filtro.value.trim() && document.activeElement === filtro) {
      e.preventDefault();
      limpiarFiltroLista();
    }
  });

  // "/" enfoca el campo desde cualquier lado del home, como en los buscadores.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || e.target.isContentEditable) return;
    if (document.getElementById("step-input").classList.contains("hidden")) return;
    e.preventDefault();
    linkInput.focus();
    linkInput.select();
  });

  // Modal "+ Agregar": Enter confirma, Escape cancela.
  const agregarInput = document.getElementById("agregar-input");
  if (agregarInput) {
    agregarInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); confirmarAgregarComentario(); }
      else if (e.key === "Escape") { e.preventDefault(); cerrarAgregarModal(); }
    });
  }

  actualizarConteo();
});
