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

// ── Etapas de selección ──────────────────────────────────────────────────────
// Una pantalla por servicio, en orden: 1) verificados sobre TODA la tanda
// generada, 2) comunes sobre lo que quedó SIN elegir en la 1, 3) (opcional) la
// tanda de WhatsApp sobre lo que sobró de las dos. Al pasar de etapa, lo elegido
// se guarda y sale de la lista; lo no elegido se muda al panel de la etapa que
// sigue. Así el vendedor nunca ve dos veces el mismo comentario disponible.
// Los textos son largos a propósito: los usa gente que no conoce el sistema y
// tiene que quedar imposible confundirse de qué se elige en cada paso y qué pasa
// con lo que NO se elige.
const ETAPAS = [
  null,
  {
    tipo: "verificado", label: "Verificados", tag: "verificados",
    paso: "PASO 1 DE 3",
    titulo: "Elegí SOLO los comentarios VERIFICADOS",
    desc: "De toda la lista de abajo, marcá únicamente los que querés mandar como <b>Comentarios Reales Verificados</b>. Los comunes NO se eligen acá.",
    tranqui: "👉 Lo que NO marques no se pierde: pasa al paso 2 para elegir los comunes.",
    subPaso: "de toda la tanda",
    siguiente: "Listo, ir al PASO 2: comunes →",
  },
  {
    tipo: "noverif", label: "Comunes", tag: "comunes",
    paso: "PASO 2 DE 3 · OPCIONAL",
    titulo: "Ahora elegí SOLO los comentarios COMUNES",
    desc: "Abajo quedaron únicamente los que <b>no</b> elegiste en el paso 1. Marcá los que querés mandar como <b>Comentarios Reales</b> (comunes, no verificados).",
    tranqui: "👉 Los verificados del paso 1 ya están guardados: acá no los volvés a ver. Este paso también es opcional: si el cliente no lleva comunes, tocá “Saltar”.",
    subPaso: "de lo que sobró",
    siguiente: "Listo, ir al PASO 3: WhatsApp →",
  },
  {
    tipo: "wa", label: "WhatsApp", tag: "para WhatsApp",
    paso: "PASO 3 DE 3 · OPCIONAL",
    titulo: "¿Querés mandar algunos por WhatsApp?",
    desc: "Abajo está lo que sobró de los pasos 1 y 2. Marcá los que quieras repartir por WhatsApp, uno por mensaje.",
    tranqui: "👉 Este paso es opcional: si no hace falta, tocá “Saltar, ir a las órdenes”.",
    subPaso: "opcional",
    siguiente: "📲 Repartir por WhatsApp",
  },
];
let etapa = 1;
// Índices elegidos en cada etapa (etapaSel[1] = verificados, [2] = comunes, [3] = WA).
let etapaSel = { 1: [], 2: [], 3: [] };
// Los items ya consumidos por una etapa salen del DOM visible pero se conservan
// acá con su estado, para poder volver atrás sin regenerar nada.
let _guardaItems = null;

function etapaTipo() { return ETAPAS[etapa].tipo; }
function etapaObjetivo() {
  if (etapa === 1) return objetivoV;
  if (etapa === 2) return objetivoNV;
  return 0;   // WhatsApp no tiene cantidad configurada en la ficha
}
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
  // Entrada de guardia: desde acá el atrás ya no puede tirar abajo la página sin
  // avisar (todo lo que sigue se generó y no se recupera con un F5).
  _pushPaso("comentarios");
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
    // Se reinicia la tanda entera: volvemos a la etapa 1 sin nada elegido.
    etapa = 1;
    etapaSel = { 1: [], 2: [], 3: [] };
    if (_guardaItems) _guardaItems.innerHTML = "";
    renderEtapa();
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
    // Falta la palabra clave: no es un error que se arregle reintentando lo
    // mismo, es un campo vacío. Volvemos a la pantalla del link con el bloque de
    // palabra clave abierto (el preview rápido puede no haberlo abierto: falla o
    // llega tarde) y la sugerencia del caption ya cargada, para que sea escribir
    // o confirmar y generar. El scrape queda cacheado, así que no se re-baja.
    if (evento.motivo === "falta_keyword") {
      volverAlInputPorKeyword(evento);
      return;
    }
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

// Vuelta a la pantalla del link cuando el backend cortó por falta de palabra
// clave. El backend es el que sabe de verdad si el cliente trabaja así, así que
// su respuesta manda sobre lo que había decidido el preview rápido.
function volverAlInputPorKeyword(evento) {
  hide("step-comentarios");
  show("step-input");
  mostrarKeyword(true);
  const campo = document.getElementById("ig-keyword");
  const hint = document.getElementById("ig-keyword-hint");
  const kw = (evento.keyword_sugerida || "").trim();
  if (kw && !campo.value.trim()) {
    campo.value = kw;
    keywordSugerida = kw;
    if (hint) {
      hint.textContent = "Detectada en el post: revisala antes de generar.";
      hint.classList.add("ig-keyword-hint--detectada");
    }
  }
  setError(evento.mensaje);
  campo.focus();
  campo.select();
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
  // Solo el panel de la etapa en curso: las otras etapas todavía no existen.
  _panelTipo(etapaTipo());
  if (esMixto) {
    _seccionItems("hombres", etapaTipo());
    _seccionItems("mujeres", etapaTipo());
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

// Panel de la lista de una etapa (verificados / comunes / WhatsApp). Cada una
// acumula sus propias secciones de género y tiene su "+ generar más".
const _TIPOS_PANEL = [
  { key: "verificado", label: "Marcá acá los VERIFICADOS", icon: "✅", corto: "V" },
  { key: "noverif",    label: "Marcá acá los COMUNES",     icon: "💬", corto: "NV" },
  { key: "wa",         label: "Marcá acá los de WHATSAPP", icon: "📲", corto: "WA" },
];
const _TIPOS_ORDEN = _TIPOS_PANEL.map(t => t.key);

// Normaliza cualquier alias al key de panel. Todo lo que no sea un tipo conocido
// cae en "verificado", que es la etapa 1.
function _tipoKey(tipo) {
  return _TIPOS_ORDEN.includes(tipo) ? tipo : "verificado";
}

function _panelTipo(tipo) {
  const key = _tipoKey(tipo);
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
        <button type="button" class="tp-all" onclick="togglePanel('${key}')">Marcar todos</button>
        <button type="button" class="tp-rnd" onclick="elegirAlAzar('${key}')">🎲 Marcar al azar</button>
        <button type="button" class="tp-mas" onclick="cargarMas('${key}')">+ generar más</button>
      </div>
      <div class="tp-progreso hidden"><span></span></div>
      <div class="tipo-panel-items"></div>`;
    // Orden fijo: verificados → comunes → WhatsApp (el mismo de las etapas).
    const pos = _TIPOS_ORDEN.indexOf(key);
    const siguiente = [...lista.querySelectorAll(".tipo-panel")]
      .find(p => _TIPOS_ORDEN.indexOf(p.dataset.tipo) > pos);
    lista.insertBefore(panel, siguiente || null);
  }
  return panel;
}

function _seccionItems(genero, tipo) {
  const tipoKey = _tipoKey(tipo);
  // WhatsApp va en UNA lista sola: el género define a qué cuenta se le asigna
  // cada comentario en el CRM, y al grupo se manda todo junto igual. Separarlo
  // en dos columnas era ruido en un paso que solo copia texto.
  const key = tipoKey === "wa"
    ? "otros"
    : ((genero === "hombres" || genero === "mujeres") ? genero : "otros");
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
  // En pantalla vive UNA sola lista: la de la etapa en curso. Las de las otras
  // etapas quedan creadas pero ocultas (las llena avanzarEtapa/volverEtapa).
  lista.querySelectorAll(".tipo-panel").forEach((panel) => {
    const total = panel.querySelectorAll(".comentario-item").length;
    const cnt = panel.querySelector(".tp-count");
    if (cnt) cnt.textContent = total;
    panel.classList.toggle("hidden", panel.dataset.tipo !== etapaTipo());
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
    const esWa = sec.closest('.tipo-panel[data-tipo="wa"]');
    const mantener = !esWa && esMixto && hayAlguno && (g === "hombres" || g === "mujeres");
    sec.classList.toggle("hidden", items.length === 0 && !mantener);
    items.forEach((it) => {
      n++;
      const e = it.querySelector(".comentario-num");
      if (e) e.textContent = n;
    });
  });
  // 2 columnas (Hombres | Mujeres) dentro de cada panel, si el cliente es mixto
  // o si ese panel ya tiene los dos géneros.
  // Manda el panel VISIBLE (el de la etapa en curso): en el paso 3 la lista es
  // una sola columna aunque el cliente sea mixto, y la tarjeta no debe quedar
  // ancha por lo que muestran los paneles ocultos.
  let mixto = false;
  lista.querySelectorAll(".tipo-panel-items").forEach((cont) => {
    const hayH = cont.querySelector('.genero-seccion[data-genero="hombres"]:not(.hidden)');
    const hayM = cont.querySelector('.genero-seccion[data-genero="mujeres"]:not(.hidden)');
    // La lista de WhatsApp nunca va en 2 columnas: no se separa por género.
    const dos = cont.closest('.tipo-panel[data-tipo="wa"]')
      ? false
      : (esMixto || !!(hayH && hayM));
    cont.classList.toggle("lista-2col", dos);
    if (!cont.closest(".tipo-panel").classList.contains("hidden")) mixto = mixto || dos;
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
  // Modo palabra clave: los comentarios REPETIDOS son el producto (40 veces la
  // misma palabra, variando la escritura). Este filtro los colapsaba a las
  // pocas formas distintas y la tanda quedaba en 4 comentarios.
  if (currentKeyword) { agregarComentario(texto, index); return; }
  const n = normComentario(texto);
  if (vistosStream.has(n)) return;
  vistosStream.add(n);
  agregarComentario(texto, index);
}

// Todo lo que llega entra a la lista de la etapa en curso: ya no hay reparto
// V/NV al generar. Lo que no se elija en esta etapa pasa entero a la siguiente.
function _tipoParaNuevo() {
  const t = _tipoKey(tipoForzado || etapaTipo());
  if (t === "verificado") asignadosV++; else if (t === "noverif") asignadosNV++;
  return t;
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
  // Dentro del objetivo de la etapa: viene marcado. El vendedor destilda lo que
  // no le guste en vez de tener que elegir 80 comentarios a mano.
  // Modo palabra clave: la tanda entera es el pedido (N veces la misma palabra),
  // no hay nada que elegir entre comentarios — van todos marcados de una.
  const objetivoEtapa = etapaObjetivo();
  const yaElegidos = document.querySelectorAll(
    "#lista-comentarios input[type=checkbox]:checked").length;
  const autoElegido = currentKeyword ? true
    : (!!objetivoEtapa && yaElegidos < objetivoEtapa);

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
  // La barra recién aparece con el primer comentario: es acá donde el stepper y
  // los textos de la etapa 1 tienen que quedar pintados. Una sola vez: repetirlo
  // por cada comentario que entra es trabajo al pedo en una tanda de 80.
  if (document.getElementById("etapa-stepper")?.classList.contains("hidden")) renderEtapa();
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

// Fija el tipo (= etapa) de un comentario y lo muda al panel que corresponde.
// Ya no hay switch V/NV por fila: el tipo lo define la etapa en la que se elige
// (etapa 1 = verificado/producto 94, etapa 2 = noverif/95, etapa 3 = WhatsApp).
function _setTipo(index, nuevo) {
  const anterior = tiposGenerados[index];
  tiposGenerados[index] = _tipoKey(nuevo);
  if (anterior === tiposGenerados[index]) return;
  const item = document.querySelector(`#lista-comentarios .comentario-item[data-index="${index}"]`);
  if (!item) return;
  _seccionItems(generosGenerados[index], tiposGenerados[index]).appendChild(item);
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

// ---------------------------------------------------------------------------
// Repartir por WhatsApp
//
// El bot manda la tanda a UN WhatsApp y desde ahí la persona la reenvía adonde
// quiera. El destino no lo decide el sistema: la Cloud API de Meta solo entrega
// a números (nunca a grupos), así que quien reparte es quien recibe.
// ---------------------------------------------------------------------------

// Los mensajes de la tanda actual y cuáles ya se mandaron. El "enviado" es lo
// único que hace utilizable la pantalla con 20 mensajes: sin la marca, después
// de tres se pierde la cuenta de por dónde iba.
let repartoItems = [];
let repartoEnviados = new Set();

// La lista a repartir la arma la ETAPA 3 (avanzarEtapa): son los comentarios
// que sobraron de las etapas 1 y 2 y que el vendedor eligió mandar al grupo.
function abrirRepartirCon(textos) {

  // Mismo formato que manda el bot (ver /api/repartir-wa): "Comentarios" + el
  // link, y después cada comentario pelado. Los dos caminos tienen que mandar
  // exactamente lo mismo, si no el grupo recibe dos formatos distintos según
  // por dónde se haya repartido.
  // El link siempre va: es lo que le da contexto al resto.
  repartoItems = [{ tipo: "link", texto: `Comentarios\n${currentUrl}`, incluido: true }];
  textos.forEach(t => repartoItems.push(typeof t === "string"
    ? { tipo: "comentario", texto: t, incluido: true }
    : { tipo: "comentario", texto: t.texto, incluido: t.incluido }));
  repartoEnviados = new Set();


  // El bloque del bot arranca plegado y NO se consulta nada suyo hasta que se
  // abre: la vía manual no depende del bot, y pedirle el teléfono a alguien que
  // solo quiere mandar de a uno es fricción sin motivo.
  document.getElementById("repartir-bulk").classList.add("hidden");
  document.getElementById("repartir-toggle")?.classList.remove("repartir-toggle--abierto");
  _botCargado = false;
  const msg = document.getElementById("repartir-bulk-msg");
  msg.className = "repartir-bulk-msg hidden";
  msg.textContent = "";

  renderRepartir();
  document.getElementById("repartir-overlay").classList.remove("hidden");
  // Ya visible: recién ahora la lista tiene alto real y se puede saber si sobra
  // contenido abajo.
  _marcarFinDeLista();
}

// Un click: el bot manda los mensajes sueltos al WhatsApp configurado. De ahí
// se reenvían con la selección múltiple de WhatsApp, que es lo que reemplaza al
// copiar-pegar de a uno.
async function mandarTandaWa() {
  const btn = document.getElementById("repartir-bulk-btn");
  const msg = document.getElementById("repartir-bulk-msg");
  if (btn.disabled) return;
  const textoOriginal = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = "Mandando…";
  msg.className = "repartir-bulk-msg hidden";

  try {
    const r = await fetch("/api/repartir-wa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: currentUrl,
        comentarios: repartoItems.filter(it => it.tipo === "comentario" && it.incluido)
                                 .map(it => it.texto),
        client: window._clientIg || "",
      }),
    });
    const data = await r.json().catch(() => ({}));

    if (r.ok) {
      marcarTodoRepartido();
      // "Salieron", NO "llegaron". Meta devuelve 200 aunque después descarte los
      // mensajes por la ventana de 24h vencida, y avisa por un webhook que no
      // escuchamos. Prometer la entrega acá era el peor error posible: el
      // vendedor veía el tilde verde y no llegaba nada.
      msg.textContent = `Salieron ${data.enviados} mensajes. Si en unos segundos no ` +
        `los ves en WhatsApp, apretá "Activar WhatsApp" acá arriba y reintentá.`;
      msg.className = "repartir-bulk-msg repartir-bulk-msg--ok";
    } else if (r.status === 207) {
      // Salió parte. Lo importante es cuántos faltan, no el detalle técnico:
      // reintentar toda la tanda duplicaría los que sí llegaron.
      const detalle = (data.fallidos || [])[0]?.detalle || "";
      msg.textContent = `Se mandaron ${data.enviados} de ${data.total}. ` +
        `Los que faltan mandalos de a uno con los botones de abajo.` + (detalle ? ` (${detalle})` : "");
      msg.className = "repartir-bulk-msg repartir-bulk-msg--warn";
    } else {
      const detalle = data.error || (data.fallidos || [])[0]?.detalle || "";
      msg.textContent = detalle || "No se pudieron mandar los mensajes.";
      msg.className = "repartir-bulk-msg repartir-bulk-msg--bad";
    }
  } catch (e) {
    msg.textContent = "No se pudo contactar al servidor. Probá de nuevo.";
    msg.className = "repartir-bulk-msg repartir-bulk-msg--bad";
  } finally {
    btn.disabled = false;
    btn.innerHTML = textoOriginal;
  }
}

// Cerrar el modal NO avanza: devuelve a la lista de comentarios del paso 3, por
// si quiere cambiar la selección y repartir otra tanda. Para seguir a las
// órdenes está el botón explícito del pie del modal.
function cerrarRepartir() {
  document.getElementById("repartir-overlay").classList.add("hidden");
  _repartoSigueAOrdenes = false;
}

// Pie del modal: termina el paso 3 (haya mandado o no) y va a las órdenes.
function continuarAOrdenes() {
  document.getElementById("repartir-overlay").classList.add("hidden");
  _repartoSigueAOrdenes = false;
  irAOrdenes();
}

function renderRepartir() {
  const lista = document.getElementById("repartir-lista");
  // El primero sin enviar. Se resalta para no perder el hilo a mitad de una
  // tanda de 20, que es donde el reparto de a uno se vuelve confuso.
  const proximo = repartoItems.findIndex((it, i) => it.incluido && !repartoEnviados.has(i));
  let n = 0;
  lista.innerHTML = repartoItems.map((it, i) => {
    const enviado = repartoEnviados.has(i);
    // La etiqueta NO lleva número: el orden ya lo da el círculo de la izquierda,
    // y numerar los comentarios aparte dejaba dos numeraciones distintas en la
    // misma fila (el círculo 2 sobre "Comentario 1", porque el link va primero).
    const etiqueta = it.tipo === "link" ? "Link del post" : "Comentario";
    const siguiente = !enviado && i === proximo;
    if (it.incluido) n++;
    const clases = ["repartir-item",
      enviado ? "repartir-item--ok" : "",
      siguiente ? "repartir-item--next" : "",
      it.incluido ? "" : "repartir-item--fuera"].filter(Boolean).join(" ");
    // El link no se puede destildar: sin él, los comentarios sueltos que llegan
    // al grupo no se sabe a qué post pertenecen.
    const izq = it.tipo === "link"
      ? `<span class="repartir-num">${enviado ? "✓" : n}</span>`
      : `<label class="repartir-check" title="Mandar este"><input type="checkbox"
           ${it.incluido ? "checked" : ""} onchange="toggleIncluido(${i})" /><span
           class="repartir-num">${enviado ? "✓" : (it.incluido ? n : "–")}</span></label>`;
    // La etiqueta va SOLO en el link del post. Repetir "COMENTARIO" en cada
    // fila era ruido: con 20 filas la palabra aparece 20 veces y no distingue
    // nada, porque todo lo demás ya es un comentario.
    return `<div class="${clases}">
      ${izq}
      <div class="repartir-cuerpo">
        ${it.tipo === "link" ? `<span class="repartir-tag">${etiqueta}</span>` : ""}
        <span class="repartir-texto">${escapeHtml(it.texto)}</span>
      </div>
      <button type="button" class="repartir-enviar" onclick="enviarWa(${i})"${it.incluido ? "" : " disabled"}>
        ${enviado ? "Reenviar" : "Enviar"}
      </button>
    </div>`;
  }).join("");

  // El progreso y el botón del bot cuentan SOLO lo tildado: si no, con 40
  // generados y 7 elegidos el contador decía "0 / 41" y no significaba nada.
  const incluidos = repartoItems.filter(it => it.incluido).length;
  const hechos = repartoItems.filter((it, i) => it.incluido && repartoEnviados.has(i)).length;
  document.getElementById("repartir-progreso").textContent = `${hechos} / ${incluidos}`;
  const nBot = document.getElementById("repartir-bulk-n");
  if (nBot) nBot.textContent = incluidos;
  const sub = document.getElementById("repartir-sub");
  if (sub) sub.textContent = `El link del post y ${incluidos - 1} ` +
    `comentario${incluidos === 2 ? "" : "s"} elegido${incluidos === 2 ? "" : "s"}.`;
  _pintarTodoEnUno();
  _marcarFinDeLista();
}

// El degradado del final solo tiene sentido si hay algo más abajo. Se recalcula
// al renderizar y al scrollear, así no queda difuminada la última fila cuando
// ya llegaste al fondo.
function _marcarFinDeLista() {
  const lista = document.getElementById("repartir-lista");
  if (!lista) return;
  // Con el modal todavía oculto (display:none) todas las medidas son 0 y la
  // cuenta daba "estás al final", así que el degradado nunca aparecía. Sin
  // altura medible no se decide nada: se recalcula cuando el modal ya se ve.
  if (!lista.clientHeight) return;
  const fin = lista.scrollTop + lista.clientHeight >= lista.scrollHeight - 4;
  lista.classList.toggle("repartir-lista--fin", fin);
  if (!lista.dataset.scrollBound) {
    lista.dataset.scrollBound = "1";
    lista.addEventListener("scroll", _marcarFinDeLista, { passive: true });
  }
}

// Arma el bloque único: el link del post y después cada comentario con su
// propio wa.me para reenviarlo. Es texto plano, así que se puede mandar por el
// mismo deep link de siempre — sin bot, sin ventana de 24h, sin lista blanca.
// Saca los emoji del renglón de LECTURA. No es cosmética porque sí: el camino
// Growi -> WhatsApp (el deep link que abre el botón) rompe los emoji del texto
// plano y llegan como el rombo de "carácter desconocido". Verificado: por el
// camino WhatsApp -> WhatsApp (los links de adentro) llegan bien, así que el
// comentario que se REENVÍA sale completo, con emoji. Acá solo se limpia lo que
// se lee para identificar cuál es cuál, donde un rombo parece un error.
//
// Se limpia únicamente lo que está fuera del plano básico de Unicode (emoji y
// poco más). Las tildes y la ñ son de 2 bytes, viajan bien y NO se tocan.
function _sinEmoji(t) {
  const limpio = String(t || "")
    .replace(/[\u{10000}-\u{10FFFF}]/gu, "")
    .replace(/[\u{FE0F}\u{20E3}\u{200D}]/gu, "")
    .replace(/ {2,}/g, " ")
    .trim();
  // Un comentario que era SOLO emoji quedaría en blanco: ahí el rombo, feo y
  // todo, dice más que un renglón vacío.
  return limpio || String(t || "");
}

function _textoTodoEnUno() {
  const coms = repartoItems.filter(it => it.tipo === "comentario" && it.incluido);
  // El encabezado también lleva su link de reenvío: es el primer mensaje que va
  // al grupo y sin esto había que copiarlo a mano, que era el único paso del
  // reparto que seguía siendo manual.
  const cabecera = repartoItems.find(it => it.tipo === "link");
  const textoCabecera = cabecera ? cabecera.texto : `Comentarios\n${currentUrl}`;
  // Los símbolos del ARMADO van en ASCII puro. Se probaron caracteres
  // tipográficos ("↪", "▸") y emoji ("👉", "📌") y los dos llegaron como el
  // cuadradito de "no soportado" al pasar por el deep link de WhatsApp — el
  // archivo y la respuesta HTTP salen bien en UTF-8, así que se pierde del otro
  // lado. Con "->" no hay nada que negociar: llega igual en todos lados.
  const partes = [`${textoCabecera}\n\n-> https://wa.me/?text=${encodeURIComponent(textoCabecera)}`];
  // Los emoji van TAL CUAL. Se probó limpiarlos del preview porque en WhatsApp
  // Desktop llegan como el rombo de "carácter desconocido", pero eso deja los
  // comentarios sin sus emoji, que es peor. En el celular puede que se vean
  // bien: el rombo se vio en Desktop, que es otro cliente. El link igual los
  // lleva codificados (%F0%9F%94%A5), así que el comentario que se reenvía
  // llega completo pase lo que pase.
  coms.forEach((it, n) => {
    // WhatsApp no permite texto sobre un link (no hay markdown ni hipervínculos):
    // siempre muestra la URL cruda. Lo único que se puede acomodar es lo de
    // alrededor, así que cada comentario lleva su propio encabezado y el link
    // va debajo, pegado al texto que le corresponde.
    //
    // El número NO va como "1. ": WhatsApp lo toma como lista numerada, le
    // aplica su propio formato y mete word-joiners invisibles en el medio. Con
    // el encabezado en su renglón aparte el texto llega tal cual se armó.
    partes.push(`${n + 1} de ${coms.length}\n` +
                `${_sinEmoji(it.texto)}\n\n` +
                `-> https://wa.me/?text=${encodeURIComponent(it.texto)}`);
  });
  return partes.join("\n\n");
}

function mandarTodoEnUno() {
  const texto = _textoTodoEnUno();
  // Se abre en otra pestaña: navegar en la misma recargaría el generador y se
  // perderían los comentarios.
  window.open(`https://wa.me/?text=${encodeURIComponent(texto)}`, "_blank", "noopener");
}

// El botón muestra cuántos van y avisa si el mensaje se está yendo de largo.
// WhatsApp corta en 4096 caracteres, pero el problema real aparece antes: el
// deep link viaja en una URL y las muy largas fallan en algunos navegadores.
function _pintarTodoEnUno() {
  const btn = document.getElementById("repartir-uno");
  const hint = document.getElementById("repartir-uno-hint");
  const nEl = document.getElementById("repartir-uno-n");
  if (!btn || !nEl) return;
  const coms = repartoItems.filter(it => it.tipo === "comentario" && it.incluido);
  nEl.textContent = coms.length;
  btn.disabled = coms.length === 0;
  const largo = _textoTodoEnUno().length;
  if (hint) {
    hint.textContent = largo > 3500
      ? `El mensaje quedaría de ${largo} caracteres y WhatsApp corta en 4096: destildá algunos o mandalos de a uno.`
      : "Un mensaje con todos, cada uno con su link para reenviarlo.";
    hint.classList.toggle("repartir-uno-hint--warn", largo > 3500);
  }
}

function toggleIncluido(i) {
  if (!repartoItems[i]) return;
  repartoItems[i].incluido = !repartoItems[i].incluido;
  renderRepartir();
}

function enviarWa(i) {
  const it = repartoItems[i];
  if (!it) return;
  // Se abre en otra pestaña: si navegáramos en la misma, volver al generador
  // recargaría la página y se perderían los comentarios generados.
  window.open(`https://wa.me/?text=${encodeURIComponent(it.texto)}`, "_blank", "noopener");
  // Se marca al abrir, no al confirmar: no hay forma de saber si el mensaje se
  // mandó de verdad. Por eso el botón queda como "Reenviar" y no desaparece.
  repartoEnviados.add(i);
  renderRepartir();
  const sig = document.querySelector("#repartir-lista .repartir-item--next");
  if (sig) sig.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

let _botCargado = false;

function toggleBot() {
  const caja = document.getElementById("repartir-bulk");
  const tog = document.getElementById("repartir-toggle");
  const abierto = !caja.classList.toggle("hidden");
  tog?.classList.toggle("repartir-toggle--abierto", abierto);
  if (abierto && !_botCargado) {
    _botCargado = true;
    cargarMiWhatsapp();   // recién acá se pide el teléfono y el estado
  }
}

// Cada vendedor recibe la tanda en SU WhatsApp. El número lo carga él mismo la
// primera vez que reparte: pedirlo acá y no en un alta previa evita que alguien
// quede sin poder usarlo esperando que un admin le cargue el dato.
function _formatearTel(n) {
  const d = String(n || "").replace(/\D/g, "");
  return d ? "+" + d : "";
}

async function cargarMiWhatsapp() {
  const caja = document.getElementById("repartir-tel");
  const ok = document.getElementById("repartir-tel-ok");
  const btn = document.getElementById("repartir-bulk-btn");
  caja.classList.add("hidden");
  ok.classList.add("hidden");

  let d = {};
  try {
    d = await (await fetch("/api/mi-whatsapp")).json();
  } catch (_) { /* sin dato mostramos el formulario igual */ }

  const numero = d.telefono || d.fallback || "";
  if (!numero && d.editable === false) {
    // Sin cuenta asociada (admin de fallback) y sin número en el .env: no hay
    // dónde guardar nada, así que no se ofrece cargar un número que se perdería.
    ok.textContent = "Tu usuario no tiene una cuenta asociada: no puedo mandarte los mensajes.";
    ok.className = "repartir-tel-ok repartir-tel-ok--bad";
    if (btn) btn.disabled = true;
    return;
  }
  if (btn) btn.disabled = false;

  if (!d.telefono) {
    // Primera vez: se pide el número y el botón de mandar espera.
    document.getElementById("repartir-tel-input").value = _formatearTel(d.fallback);
    caja.classList.remove("hidden");
    if (btn) btn.disabled = true;
    document.getElementById("repartir-ventana").classList.add("hidden");
    return;
  }

  ok.innerHTML = `Te los mando a <b>${escapeHtml(_formatearTel(d.telefono))}</b> ` +
    `<button type="button" class="repartir-activar" onclick="editarMiWhatsapp()">cambiar</button>`;
  ok.className = "repartir-tel-ok";
  pintarVentanaWa();
}

function editarMiWhatsapp() {
  const caja = document.getElementById("repartir-tel");
  const ok = document.getElementById("repartir-tel-ok");
  const actual = (ok.querySelector("b") || {}).textContent || "";
  document.getElementById("repartir-tel-input").value = actual;
  caja.classList.remove("hidden");
  ok.classList.add("hidden");
  document.getElementById("repartir-tel-input").focus();
}

async function guardarMiWhatsapp() {
  const input = document.getElementById("repartir-tel-input");
  const err = document.getElementById("repartir-tel-error");
  err.classList.add("hidden");
  try {
    const r = await fetch("/api/mi-whatsapp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ telefono: input.value }),
    });
    const d = await r.json();
    if (!r.ok) {
      err.textContent = d.error || "No se pudo guardar el número.";
      err.classList.remove("hidden");
      return;
    }
    document.getElementById("repartir-tel").classList.add("hidden");
    await cargarMiWhatsapp();
  } catch (e) {
    err.textContent = "No se pudo contactar al servidor.";
    err.classList.remove("hidden");
  }
}

// Muestra si la ventana de 24h está abierta, antes de que el vendedor apriete
// el botón. Sin esto se entera después, cuando los mensajes ya "salieron bien"
// y no llegó nada.
async function pintarVentanaWa() {
  const box = document.getElementById("repartir-ventana");
  if (!box) return;
  box.className = "repartir-ventana hidden";
  let e;
  try {
    const r = await fetch("/api/wa-estado");
    e = await r.json();
  } catch (_) {
    return;   // sin datos no inventamos nada
  }

  // Link que abre el chat del bot con un "hola" escrito: un toque, y la ventana
  // queda abierta. Es más corto que mandar un template y esperar a responderlo.
  const link = e.numero_bot
    ? ` <a class="repartir-activar" target="_blank" rel="noopener"
           href="https://wa.me/${e.numero_bot}?text=hola">Abrir el chat del bot</a>`
    : ` <button type="button" class="repartir-activar" onclick="activarWa()">Activar WhatsApp</button>`;

  if (e.abierta === true) {
    const h = Math.floor((e.minutos_restantes || 0) / 60);
    box.innerHTML = `✓ WhatsApp activo — te quedan ${h > 0 ? h + " h" : (e.minutos_restantes || 0) + " min"}.`;
    box.className = "repartir-ventana repartir-ventana--ok";
  } else if (e.abierta === false) {
    box.innerHTML = `⚠ WhatsApp inactivo: los mensajes NO te van a llegar.${link}`;
    box.className = "repartir-ventana repartir-ventana--bad";
  } else {
    // null = el webhook no llega hasta acá. Decir "cerrada" sería inventar.
    box.innerHTML = `No sé si WhatsApp está activo (falta configurar el webhook).
      Si la tanda no te llega,${link} y respondelo.`;
    box.className = "repartir-ventana repartir-ventana--warn";
  }
}

// Abre la ventana de 24h de Meta. Manda un template (que sí atraviesa la
// ventana); respondiéndolo, el texto libre vuelve a entregarse por 24 horas.
async function activarWa() {
  const msg = document.getElementById("repartir-bulk-msg");
  msg.textContent = "Mandando el mensaje de activación…";
  msg.className = "repartir-bulk-msg repartir-bulk-msg--warn";
  try {
    const r = await fetch("/api/activar-wa", { method: "POST" });
    const data = await r.json().catch(() => ({}));
    if (r.ok) {
      msg.textContent = "Te mandé un mensaje al WhatsApp. Respondelo (con cualquier " +
        "cosa) y después volvé a apretar el botón verde.";
      msg.className = "repartir-bulk-msg repartir-bulk-msg--ok";
    } else {
      msg.textContent = data.error || "No se pudo mandar el mensaje de activación.";
      msg.className = "repartir-bulk-msg repartir-bulk-msg--bad";
    }
  } catch (e) {
    msg.textContent = "No se pudo contactar al servidor.";
    msg.className = "repartir-bulk-msg repartir-bulk-msg--bad";
  }
}

function marcarTodoRepartido() {
  repartoItems.forEach((it, i) => { if (it.incluido) repartoEnviados.add(i); });
  renderRepartir();
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
          // En modo palabra clave todo es la misma palabra: el filtro de
          // casi-duplicados tiraría la tanda entera.
          if (currentKeyword || generoDeHeader(evento.texto)) {
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
      const objetivo = etapaObjetivo();
      badge.textContent = objetivo ? `${sel} de ${objetivo} elegidos` : `${sel} elegidos`;
      badge.classList.toggle("hidden", checks.length === 0);
      // Verde cuando coincide con lo que pide la ficha del cliente.
      badge.classList.toggle("tp-sel--ok", !!objetivo && sel === objetivo);
    }
    const btn = panel.querySelector(".tp-all");
    if (btn) {
      const todos = checks.length > 0 && sel === checks.length;
      btn.textContent = todos ? "Desmarcar todos" : "Marcar todos";
      btn.disabled = checks.length === 0;
    }
    // Barra de avance hacia lo que pide la ficha del cliente: el número solo
    // ("12 de 40") obliga a hacer la cuenta cada vez que se marca uno.
    const barra = panel.querySelector(".tp-progreso");
    if (barra) {
      const objetivo = etapaObjetivo();
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

// Sortea la selección de un panel: desmarca todo y marca N al azar. N es el
// objetivo de la etapa (lo que pidió el cliente); si no hay objetivo cargado se
// respeta la cantidad que ya estaba marcada, así el botón solo "re-sortea".
// Pregunta cuántos marcar. La cantidad cambia post a post, así que se propone
// la de la ficha del cliente (o lo que ya haya tildado) pero se puede escribir
// cualquier otra.
let _azarTipo = null;

function elegirAlAzar(tipo) {
  const panel = document.querySelector(`.tipo-panel[data-tipo="${tipo}"]`);
  if (!panel) return;
  const total = panel.querySelectorAll("input[type=checkbox]").length;
  if (!total) return;
  _azarTipo = tipo;
  const yaSel = [...panel.querySelectorAll("input[type=checkbox]")].filter(c => c.checked).length;
  const sugerido = Math.min(etapaObjetivo() || yaSel || 10, total);
  const input = document.getElementById("azar-cant");
  input.max = total;
  input.value = sugerido;
  document.getElementById("azar-sub").textContent =
    `Sobre ${total} de la lista${etapaObjetivo() ? ` · la ficha del cliente pide ${etapaObjetivo()}` : ""}`;
  document.getElementById("azar-error").classList.add("hidden");
  show("azar-overlay");
  input.focus();
  input.select();
}

function cerrarAzar() {
  hide("azar-overlay");
  _azarTipo = null;
}

function confirmarAzar() {
  const panel = document.querySelector(`.tipo-panel[data-tipo="${_azarTipo}"]`);
  if (!panel) return cerrarAzar();
  const total = panel.querySelectorAll("input[type=checkbox]").length;
  const n = parseInt(document.getElementById("azar-cant").value, 10);
  if (!Number.isInteger(n) || n < 1 || n > total) {
    const err = document.getElementById("azar-error");
    err.textContent = `Poné un número entre 1 y ${total}`;
    err.classList.remove("hidden");
    return;
  }
  const tipo = _azarTipo;
  cerrarAzar();
  _marcarAlAzar(tipo, n);
}

// Enter confirma: es un solo campo y se usa a repetición.
document.addEventListener("keydown", e => {
  if (document.getElementById("azar-overlay")?.classList.contains("hidden")) return;
  if (e.key === "Enter") { e.preventDefault(); confirmarAzar(); }
  if (e.key === "Escape") cerrarAzar();
});

function _marcarAlAzar(tipo, n) {
  const panel = document.querySelector(`.tipo-panel[data-tipo="${tipo}"]`);
  if (!panel) return;
  const checks = [...panel.querySelectorAll("input[type=checkbox]")];
  if (!checks.length) return;
  n = Math.min(n, checks.length);
  // Fisher-Yates sobre una copia: los primeros n quedan marcados.
  const mezcla = checks.slice();
  for (let i = mezcla.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [mezcla[i], mezcla[j]] = [mezcla[j], mezcla[i]];
  }
  mezcla.forEach((c, i) => {
    c.checked = i < n;
    c.closest(".comentario-item").classList.toggle("selected", c.checked);
  });
  actualizarConteo();
}

function actualizarConteo() {
  const sel = contarSeleccionados();
  _refrescarConteoSecciones();
  const label = document.getElementById("count-label");
  if (label) {
    // Lo elegido en ESTA etapa (los de las etapas anteriores ya están guardados
    // y se muestran en el stepper).
    const objetivo = etapaObjetivo();
    label.textContent = sel === 0
      ? `0 ${ETAPAS[etapa].tag}`
      : `${sel} ${ETAPAS[etapa].tag}${objetivo ? ` de ${objetivo}` : ""}`;
    label.classList.toggle("count-label--lleno", sel > 0);   // pill amarilla con selección
  }
  const btnPublicar = document.getElementById("btn-publicar");
  if (btnPublicar) btnPublicar.disabled = sel === 0;

  // Resumen de la barra fija: lo elegido en esta etapa + lo que ya quedó cerrado
  // en las anteriores, para no tener que subir hasta el stepper a controlarlo.
  const resumen = document.getElementById("etapa-nav-resumen");
  if (resumen) {
    const partes = [];
    for (let n = 1; n < etapa; n++) {
      const q = (etapaSel[n] || []).length;
      if (q) partes.push(`${q} ${ETAPAS[n].tag} ✓`);
    }
    partes.push(`<b>${sel} ${ETAPAS[etapa].tag}</b> ahora`);
    resumen.innerHTML = partes.join(" · ");
  }

  // Lista vacía = no sobró nada de los pasos anteriores.
  const vacia = document.getElementById("etapa-vacia");
  if (vacia) {
    const hay = document.querySelector("#lista-comentarios .comentario-item");
    const mostrar = !hay && etapa > 1;
    vacia.classList.toggle("hidden", !mostrar);
    if (mostrar) {
      document.getElementById("etapa-vacia-txt").textContent =
        `No sobró ningún comentario para el paso ${etapa}: los elegiste todos en los pasos anteriores. ` +
        (etapa === 3
          ? "Podés saltar este paso e ir directo a las órdenes."
          : "Generá más, o volvé al paso anterior y sacá algunos.");
    }
  }
  _syncSaltar(sel);
  // El reparto por WhatsApp ya no vive acá: es la etapa 2, después de publicar.
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
  try {
    panel.classList.toggle("collapsed", localStorage.getItem("panelSelColapsado") === "1");
  } catch (e) {}
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

function togglePanelSeleccionados() {
  const panel = document.getElementById("panel-seleccionados");
  if (!panel) return;
  const colapsado = panel.classList.toggle("collapsed");
  try { localStorage.setItem("panelSelColapsado", colapsado ? "1" : "0"); } catch (e) {}
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

  // Estos son los objetivos de cada etapa: la etapa 1 marca sola esa cantidad de
  // verificados y la 2 esa cantidad de comunes. Sin ficha cargada quedan en 0 y
  // el vendedor elige a mano, como antes.
  objetivoV = verif || 0;
  objetivoNV = comunes || 0;
  const inputEtapa = document.getElementById("cant-etapa");
  if (inputEtapa) inputEtapa.value = etapaObjetivo();
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

function _errorEtapa(txt) {
  const msg = document.getElementById("turnos-msg");
  if (!msg) return;
  if (!txt) { msg.classList.add("hidden"); return; }
  msg.textContent = txt;
  msg.classList.remove("hidden");
}

// Tomás: contar a mano para llegar a "60 verificados" es un viaje. Con esto se
// pide la cantidad exacta de ESTA etapa y el reparto lo hace solo: elige al azar
// CUÁLES comentarios van (para no mandar siempre los mismos) respetando el número.
function repartirEtapa() {
  _errorEtapa(null);
  const raw = parseInt(document.getElementById("cant-etapa")?.value, 10);
  const n = Math.max(0, Number.isFinite(raw) ? raw : 0);
  const nombre = ETAPAS[etapa].tag;

  if (n === 0) return _errorEtapa(`Escribí cuántos comentarios ${nombre} querés mandar.`);
  if (etapa === 2) {
    const tope = tandaUnicaActiva() ? TURNO_MAX : TURNOS_MAX;
    if (n > tope) {
      return _errorEtapa(tandaUnicaActiva()
        ? `En una sola tanda el tope es ${TURNO_MAX} comunes (lo que entra en un turno) y pediste ${n}.`
        : `Los comunes tienen un tope de ${TURNOS_MAX} por día (40 + 40) y pediste ${n}. Bajá a ${TURNOS_MAX} o menos.`);
    }
  }

  const items = [...document.querySelectorAll(
    `.tipo-panel[data-tipo="${etapaTipo()}"] .comentario-item`)];
  if (n > items.length) {
    return _errorEtapa(`Pediste ${n} ${nombre} y hay ${items.length} disponibles en esta etapa. Tocá "+ generar más", o bajá la cantidad.`);
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

  actualizarConteo();
}

// ── Navegación entre etapas ──────────────────────────────────────────────────
// Los items elegidos se guardan fuera de la lista (no se destruyen: volver atrás
// tiene que devolverlos tal cual, sin regenerar). Los NO elegidos se mudan al
// panel de la etapa siguiente.
function _guarda() {
  if (!_guardaItems) {
    _guardaItems = document.createElement("div");
    _guardaItems.id = "etapa-guarda";
    _guardaItems.className = "hidden";
    document.body.appendChild(_guardaItems);
  }
  return _guardaItems;
}

function _itemsEtapa() {
  return [...document.querySelectorAll(
    `.tipo-panel[data-tipo="${etapaTipo()}"] .comentario-item`)];
}

function _indiceDe(item) {
  return parseInt(item.dataset.index, 10);
}

// Refresca todo lo que depende de en qué etapa estamos: stepper, textos, botones.
function renderEtapa() {
  const meta = ETAPAS[etapa];

  document.getElementById("etapa-stepper")?.classList.remove("hidden");
  document.querySelectorAll("#etapa-stepper .etapa-paso").forEach((p) => {
    const n = parseInt(p.dataset.etapa, 10);
    const hecho = n < etapa;
    p.classList.toggle("etapa-paso--activo", n === etapa);
    p.classList.toggle("etapa-paso--hecho", hecho);
    // Un paso ya cerrado muestra CUÁNTOS quedaron elegidos: es la única forma de
    // controlar de un vistazo lo que se va a mandar sin volver atrás.
    const sub = p.querySelector(".ep-sub");
    if (sub) {
      sub.textContent = hecho
        ? `${(etapaSel[n] || []).length} elegidos ✓`
        : (ETAPAS[n]?.subPaso || sub.textContent);
      sub.classList.toggle("ep-sub--ok", hecho);
    }
  });

  // Cartel grande: qué se elige AHORA y qué pasa con lo que no se elige.
  const cartel = document.getElementById("etapa-cartel");
  if (cartel) {
    cartel.classList.remove("hidden");
    cartel.classList.toggle("etapa-cartel--opcional", etapa >= 2);
    document.getElementById("etapa-cartel-paso").textContent = meta.paso;
    document.getElementById("etapa-cartel-titulo").textContent = meta.titulo;
    document.getElementById("etapa-cartel-desc").innerHTML = meta.desc;
    document.getElementById("etapa-cartel-tranqui").textContent = meta.tranqui;
    const saltarCartel = document.getElementById("btn-cartel-saltar");
    if (saltarCartel) {
      saltarCartel.classList.toggle("hidden", etapa === 1);
      saltarCartel.textContent = etapa === 2
        ? "Saltar, ir al PASO 3: WhatsApp →"
        : "Saltar, ir a las órdenes →";
    }
  }

  const titulo = document.getElementById("reparto-titulo");
  if (titulo) {
    titulo.textContent = etapa === 3
      ? "¿Cuántos mando por WhatsApp?"
      : `¿Cuántos ${meta.tag} mando?`;
  }
  // El título de la tarjeta también dice la etapa: en mobile el cartel puede
  // quedar arriba, fuera de la pantalla, mientras se scrollea la lista.
  const cardTitle = document.querySelector("#step-comentarios .comments-card .card-title");
  if (cardTitle) cardTitle.textContent = `Paso ${etapa}: elegí los ${meta.tag}`;
  const tag = document.getElementById("reparto-tag");
  if (tag) {
    tag.textContent = meta.tag;
    tag.className = "reparto-tag " + (etapa === 1 ? "reparto-tag--v" : "reparto-tag--nv");
  }
  // El aviso de turnos y el check de "una sola tanda" son SOLO de los comunes.
  document.getElementById("tanda-unica-box")?.classList.toggle("hidden", etapa !== 2);
  document.getElementById("turnos-toggle")?.classList.toggle("hidden", etapa !== 2 || tandaUnicaActiva());
  _errorEtapa(null);

  const volver = document.getElementById("btn-etapa-volver");
  if (volver) volver.classList.toggle("hidden", etapa === 1);
  const saltar = document.getElementById("btn-etapa-saltar");
  if (saltar) {
    saltar.classList.toggle("hidden", etapa === 1);
    saltar.textContent = etapa === 2 ? "Saltar, ir a WhatsApp" : "Saltar, ir a las órdenes";
  }
  const btn = document.getElementById("btn-publicar");
  if (btn) btn.textContent = meta.siguiente;

  // "Cargar más" global tiene que caer en la lista de la etapa en curso.
  const cargar = document.getElementById("btn-cargar-mas");
  if (cargar) cargar.setAttribute("onclick", `cargarMas('${meta.tipo}')`);

  actualizarConteo();
}

// Guarda lo elegido en esta etapa y pasa a la siguiente con los sobrantes.
function _pasarA(siguiente) {
  const elegidos = [], sobrantes = [];
  _itemsEtapa().forEach((item) => {
    (item.querySelector("input[type=checkbox]")?.checked ? elegidos : sobrantes).push(item);
  });

  etapaSel[etapa] = elegidos.map(_indiceDe).filter(i => !Number.isNaN(i));
  elegidos.forEach((item) => {
    item.dataset.etapaElegida = String(etapa);
    _guarda().appendChild(item);
  });

  etapa = siguiente;
  // Los sobrantes cambian de tipo (= de servicio) y de panel.
  sobrantes.forEach((item) => {
    const i = _indiceDe(item);
    tiposGenerados[i] = etapaTipo();
    const chk = item.querySelector("input[type=checkbox]");
    if (chk) chk.checked = false;
    item.classList.remove("selected");
    _seccionItems(generosGenerados[i], etapaTipo()).appendChild(item);
  });

  _refrescarSecciones();
  renderEtapa();
  // Un fundido corto al cambiar de paso: sin él la lista se recorta de golpe y
  // no se percibe que cambió de pantalla (es la misma tarjeta).
  const card = document.querySelector("#step-comentarios .comments-card");
  if (card) {
    card.classList.remove("etapa-entrando");
    void card.offsetWidth;   // fuerza el reinicio de la animación
    card.classList.add("etapa-entrando");
  }
  // La cantidad de la etapa nueva sale de la ficha del cliente; si hay, se
  // marcan solos igual que en la etapa 1 y el vendedor solo ajusta.
  const input = document.getElementById("cant-etapa");
  const objetivo = Math.min(etapaObjetivo(), _itemsEtapa().length);
  if (input) input.value = objetivo;
  if (objetivo > 0) repartirEtapa();
  window.scrollTo({ top: 0, behavior: "smooth" });
  _pushPaso("comentarios");
}

// Botón principal. En la etapa 3 no publica: abre el reparto por WhatsApp.
function avanzarEtapa() {
  _errorEtapa(null);
  // Si no sobró nada para esta etapa no se puede elegir: se deja pasar de largo.
  const hayItems = _itemsEtapa().length > 0;
  // Sólo el paso 1 (verificados) es obligatorio: los comunes y WhatsApp se
  // saltean si el cliente no los lleva.
  if (hayItems && contarSeleccionados() === 0 && etapa === 1) {
    return _errorEtapa(`Elegí al menos un comentario ${ETAPAS[etapa].tag}.`);
  }
  if (!hayItems && etapa >= 2) return saltarEtapa();

  if (etapa === 1) {
    // Ya eligió los verificados: la IA no tiene que seguir generando.
    cancelarGeneracion();
    return _pasarA(2);
  }

  if (etapa === 2) {
    const n = contarSeleccionados();
    const tope = tandaUnicaActiva() ? TURNO_MAX : TURNOS_MAX;
    if (n > tope) {
      return _errorEtapa(tandaUnicaActiva()
        ? `En una sola tanda podés mandar hasta ${TURNO_MAX} comunes (tope de un turno). Tenés ${n} — sacá ${n - TURNO_MAX} o destildá "una sola tanda".`
        : `Podés mandar hasta ${TURNOS_MAX} comunes por día (40 + 40). Tenés ${n} seleccionados — sacá ${n - TURNOS_MAX}.`);
    }
    return _pasarA(3);
  }

  // Etapa 3: la tanda de WhatsApp. Al cerrar el reparto sigue solo a las órdenes.
  const textos = _itemsEtapa()
    .filter(it => it.querySelector("input[type=checkbox]")?.checked)
    .map(it => ({ texto: comentariosGenerados[_indiceDe(it)], incluido: true }))
    .filter(t => t.texto);
  if (!textos.length) {
    return _errorEtapa("Elegí los comentarios que querés repartir por WhatsApp, o tocá “Saltar”.");
  }
  etapaSel[3] = _itemsEtapa()
    .filter(it => it.querySelector("input[type=checkbox]")?.checked)
    .map(_indiceDe);
  _repartoSigueAOrdenes = true;
  abrirRepartirCon(textos);
}

// En los pasos opcionales (2 y 3), si ya hay comentarios marcados "Saltar" no
// tiene sentido: saltear los tiraría a la basura. Con selección el mismo botón
// pasa a ser el de avanzar (paso 2 → WhatsApp, paso 3 → mandar la tanda), y
// vuelve a "Saltar" si se destilda todo.
function _syncSaltar(sel) {
  const avanzar = etapa >= 2 && sel > 0;
  const txtNav = etapa === 2
    ? (avanzar ? "Seguir al PASO 3: WhatsApp" : "Saltar, ir a WhatsApp")
    : (avanzar ? "📲 Enviar por WhatsApp" : "Saltar, ir a las órdenes");
  const txtCartel = etapa === 2
    ? (avanzar ? "Seguir con los elegidos al PASO 3: WhatsApp →" : "Saltar, ir al PASO 3: WhatsApp →")
    : (avanzar ? "📲 Enviar los elegidos por WhatsApp →" : "Saltar, ir a las órdenes →");

  // Con selección, el botón principal de la barra ("Listo, ir al PASO 3…") ya
  // hace exactamente lo mismo: dejar los dos deja dos botones idénticos pegados.
  const nav = document.getElementById("btn-etapa-saltar");
  if (nav && etapa >= 2) {
    nav.classList.toggle("hidden", avanzar);
    nav.textContent = txtNav;
    nav.classList.remove("btn-publish");
    nav.classList.add("btn-ghost");
  }
  const cartel = document.getElementById("btn-cartel-saltar");
  if (cartel && etapa >= 2) cartel.textContent = txtCartel;
}

// Handler de los dos botones "Saltar": con selección avanza (en el paso 2 al de
// WhatsApp, en el 3 manda la tanda) en vez de saltear.
function saltarOEnviar() {
  if (etapa >= 2 && contarSeleccionados() > 0) return avanzarEtapa();
  saltarEtapa();
}

// Los pasos 2 y 3 son opcionales: "Saltar" avanza sin elegir nada. Desde los
// comunes va al paso 3; desde WhatsApp, derecho a las órdenes.
function saltarEtapa() {
  if (etapa === 2) {
    deseleccionarTodos();
    return _pasarA(3);
  }
  return saltarEtapaWa();
}

function saltarEtapaWa() {
  etapaSel[3] = [];
  irAOrdenes();
}

// Vuelve a la etapa anterior: los sobrantes de la actual y lo que se había
// elegido en la anterior vuelven juntos a esa lista, con su tilde intacto.
function volverEtapa() {
  if (etapa === 1) return;
  // Si el atrás del navegador está armado para esta etapa, lo consumimos: botón
  // y flecha tienen que dejar el historial igual.
  if (history.state?.step === "comentarios" && history.state?.etapa === etapa) {
    return history.back();
  }
  _volverEtapaUI();
}

function _volverEtapaUI() {
  if (etapa === 1) return;
  const previa = etapa - 1;
  const tipoPrevio = ETAPAS[previa].tipo;

  _itemsEtapa().forEach((item) => {
    const i = _indiceDe(item);
    tiposGenerados[i] = tipoPrevio;
    const chk = item.querySelector("input[type=checkbox]");
    if (chk) chk.checked = false;
    item.classList.remove("selected");
    _seccionItems(generosGenerados[i], tipoPrevio).appendChild(item);
  });

  [..._guarda().querySelectorAll(`.comentario-item[data-etapa-elegida="${previa}"]`)]
    .forEach((item) => {
      const i = _indiceDe(item);
      delete item.dataset.etapaElegida;
      tiposGenerados[i] = tipoPrevio;
      const chk = item.querySelector("input[type=checkbox]");
      if (chk) chk.checked = true;
      item.classList.add("selected");
      _seccionItems(generosGenerados[i], tipoPrevio).appendChild(item);
    });

  etapaSel[etapa] = [];
  etapaSel[previa] = [];
  etapa = previa;
  _refrescarSecciones();
  renderEtapa();
  const input = document.getElementById("cant-etapa");
  if (input) input.value = contarSeleccionados() || etapaObjetivo();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function deseleccionarTodos() {
  document.querySelectorAll("#lista-comentarios input[type=checkbox]").forEach((c) => {
    c.checked = false;
    c.closest(".comentario-item").classList.remove("selected");
  });
  actualizarConteo();
}

// El botón principal ahora avanza de etapa (avanzarEtapa). Se deja publicar()
// como alias por si quedó algún enganche viejo apuntando acá.
function publicar() { avanzarEtapa(); }

let _repartoSigueAOrdenes = false;

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
// Con "una sola tanda" no hay reparto: todo entra en el turno de ahora, así que
// el tope es el de UN turno (la mitad del diario).
const TURNO_MAX = TURNOS_MAX / 2;

function tandaUnicaActiva() {
  return !!document.getElementById("chk-tanda-unica")?.checked;
}

function onTandaUnicaChange() {
  const box = document.getElementById("tanda-unica-box");
  const on  = tandaUnicaActiva();
  box?.classList.toggle("tanda-unica--on", on);
  const hint = document.getElementById("tanda-unica-hint");
  if (hint) {
    hint.textContent = on
      ? `Todos los comunes salen juntos en este turno. Máximo ${TURNO_MAX} (es el tope de un turno).`
      : "Usalo cuando el otro turno ya pasó. Sin esto se parten 50/50 y la mitad queda para el próximo turno.";
  }
  // El cartel de "siempre 2 tandas" pasa a ser mentira con el check puesto.
  document.getElementById("turnos-toggle")?.classList.toggle("hidden", on || etapa !== 2);
  _errorEtapa(null);
}



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

// Tanda única: sale toda en el turno de ahora. Fuera de horario no hay "ahora",
// así que se programa al próximo turno que abra (la primera del plan normal).
function _turnoUnicoSpec() {
  return _turnosPlan()[0];
}

async function irAOrdenes() {
  // La selección ya la fijaron las etapas 1 y 2 (por índice, no por posición del
  // DOM: la lista se muestra mezclada visualmente). La etapa 3 es solo WhatsApp
  // y no genera órdenes.
  const idxVerif   = (etapaSel[1] || []).filter(i => !Number.isNaN(i));
  const idxNoVerif = (etapaSel[2] || []).filter(i => !Number.isNaN(i));
  if (idxVerif.length === 0 && idxNoVerif.length === 0) return;

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

  // Los no-verificados SIEMPRE se reparten en turnos (mañana/tarde), sin toggle.
  // Tope de no-verif por día (40 mañana + 40 tarde).
  // ...salvo que se haya pedido explícitamente una sola tanda, que entra entera
  // en el turno de ahora y por eso tiene el tope de un turno, no el diario.
  const unaTanda = tandaUnicaActiva();
  const topeNoVerif = unaTanda ? TURNO_MAX : TURNOS_MAX;
  _errorEtapa(null);
  if (idxNoVerif.length > topeNoVerif) {
    _errorEtapa(unaTanda
      ? `En una sola tanda podés mandar hasta ${TURNO_MAX} no verificados (es el tope de un turno). Tenés ${idxNoVerif.length} seleccionados — sacá ${idxNoVerif.length - TURNO_MAX} o destildá "una sola tanda".`
      : `Podés mandar hasta ${TURNOS_MAX} no verificados por día (40 + 40). Tenés ${idxNoVerif.length} seleccionados — sacá ${idxNoVerif.length - TURNOS_MAX}.`);
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
  if (idxNoVerif.length && unaTanda) {
    pushOrdenComentarios(idxNoVerif, 95, "Comentarios Reales", _turnoUnicoSpec(), null);
  } else if (idxNoVerif.length) {
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

  // Traer productos + rangos tarda; sin feedback parece que la página se trabó.
  const ovOrdenes = document.getElementById("ordenes-overlay");
  const ovTexto   = document.getElementById("ordenes-loading-text");
  ovOrdenes?.classList.remove("hidden");
  try {
    if (ovTexto) ovTexto.textContent = "Cargando productos...";
    await actualizarProductos();
    // Órdenes de tráfico precreadas con los rangos configurados (del cliente si el
    // post es de uno, del genérico si no). Va después de actualizarProductos
    // porque necesita el catálogo para resolver el producto de cada tipo.
    if (ovTexto) ovTexto.textContent = "Armando órdenes de tráfico...";
    await _precrearOrdenesDeRangos();
    renderOrdenes();
    prepararVentaPicker();
  } finally {
    ovOrdenes?.classList.add("hidden");
  }

  // Ocultar panel de seleccionados y counter al pasar al form
  document.getElementById("panel-seleccionados").classList.add("hidden");
  document.getElementById("comments-counter").classList.add("hidden");
  document.getElementById("status-listo").classList.add("hidden");

  hide("step-comentarios");
  show("step-ordenes");
  _pushPaso("ordenes");
}

// ── Atrás del navegador ──────────────────────────────────────────────────────
// Sin esto, la flecha de atrás se lleva puesta la página entera: hay que volver
// a pegar el link, re-scrapear y re-generar todo. Cada avance (etapa 1→2, 2→3,
// comentarios→órdenes) deja una entrada en el historial, así el atrás retrocede
// UN paso, igual que los botones de la pantalla.
function _pushPaso(step) {
  const st = { step, etapa };
  if (history.state?.step === step && history.state?.etapa === etapa) return;
  history.pushState(st, "", location.href);
}

window.addEventListener("popstate", () => {
  if (!document.getElementById("step-ordenes").classList.contains("hidden")) {
    return _volverAComentariosUI();   // órdenes → comentarios (etapa 3)
  }
  if (!document.getElementById("step-comentarios").classList.contains("hidden") && etapa > 1) {
    return _volverEtapaUI();          // etapa N → etapa N-1
  }
  // No hay paso anterior. Si ya hay comentarios generados, salir sería tirar
  // todo el trabajo: se queda donde está y se avisa.
  if (comentariosGenerados.length) {
    history.pushState({ step: "inicio", etapa }, "", location.href);
    _avisoAtras();
  }
});

function _avisoAtras() {
  const toast = document.getElementById("copy-toast");
  if (!toast) return;
  toast.textContent = "Ya estás en el primer paso";
  toast.classList.remove("hidden");
  toast.classList.add("show");
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => { toast.classList.add("hidden"); toast.textContent = "✓ Copiado"; }, 200);
  }, 1600);
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
  // El rango es por producto: al cambiar de red social el anterior ya no vale y
  // no tiene que clampear la cantidad del producto nuevo.
  _lastCantMin = null;
  _lastCantMax = null;

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
          // El backend manda {"error": "..."} con el motivo real (proxy caído,
          // credenciales, CRM abajo). Antes se pisaba con un "Error al cargar
          // productos" pelado y no se sabía qué estaba fallando.
          const data = await resp.json().catch(() => null);
          if (!resp.ok || !data || data.error)
            throw new Error(data?.error || `Error al cargar productos (HTTP ${resp.status})`);
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
let _lastCantMax = null; // último cantmax fetched, para el clamp al salir del campo

// Lleva la cantidad al rango del producto. Se llama SOLO cuando el usuario no
// está tipeando (blur / al agregar la orden): si corre durante la carga pisa
// el número a medio escribir.
function _ajustarCantidadAlRango() {
  const cantEl = document.getElementById("orden-cantidad");
  if (!cantEl || !cantEl.value) return;
  const min = _lastCantMin || 0;
  const max = _lastCantMax || 0;
  if (!min && !max) return;
  const cant = parseInt(cantEl.value) || 0;
  if (min && max && min === max) cantEl.value = min;
  else if (min && cant < min)    cantEl.value = min;
  else if (max && cant > max)    cantEl.value = max;
}

function onCantidadBlur() {
  _ajustarCantidadAlRango();
  obtenerCosto();
}
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
      _lastCantMax = max;
      hint.textContent = `Min: ${min.toLocaleString()} — Max: ${max.toLocaleString()}`;
      hint.classList.remove("hidden");

      // BUG (Lautaro, 08/08): el ajuste al rango corría mientras se tipeaba y
      // pisaba el campo. Escribías "1500", a los 300ms de pausa saltaba esta
      // rama con lo escrito hasta ahí ("1") y lo reemplazaba por el mínimo —
      // de ahí los números sueltos (un 7, un 1) apareciendo solos. Con el campo
      // enfocado no se toca: el clamp se hace al salir (onCantidadBlur) y antes
      // de agregar la orden, que es cuando el número ya está completo.
      if (document.activeElement !== cantEl) _ajustarCantidadAlRango();
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
  _llenarSelectVentas(sel)
    .then(ventas => {
      if (!ventas.length) {
        retry.classList.remove("hidden");
        _ventaPickerMsg("No encontramos campañas en tu cuenta del CRM. Creá una o pedile al admin que te asigne el cliente.", true);
        return;
      }
      // Si ya había una elegida en este post, la mantenemos.
      if (window._ventaElegida) { sel.value = window._ventaElegida; _mostrarSaldoVenta(window._ventaElegida); }
    })
    .catch(() => {
      retry.classList.remove("hidden");
      _ventaPickerMsg("No pudimos leer tus campañas del CRM.", true);
    });
}

// Llena un <select> con las campañas de la cuenta. Lo usan el picker del paso de
// órdenes y el de "reintentar con otra campaña" de la pantalla de resultado:
// son el mismo listado y el mismo formato, y duplicarlo ya nos había dejado dos
// versiones distintas de la etiqueta.
function _llenarSelectVentas(sel) {
  sel.disabled = true;
  sel.innerHTML = `<option value="">Cargando campañas…</option>`;
  return fetch("/api/ventas")
    .then(r => r.json())
    .then(d => {
      const ventas = d.ventas || [];
      _ventasSaldo = {};
      ventas.forEach(v => { _ventasSaldo[String(v.idventa)] = parseFloat(v.disponible) || 0; });
      if (!ventas.length) {
        sel.innerHTML = `<option value="">No hay campañas disponibles</option>`;
        return ventas;
      }
      sel.disabled = false;
      const saldo = (v) => `$${(parseFloat(v.disponible) || 0).toFixed(2)}`;
      sel.innerHTML = `<option value="">— Elegí una campaña —</option>` +
        ventas.map(v => `<option value="${escapeHtml(v.idventa)}">` +
          `#${escapeHtml(v.idventa)} · ${saldo(v)} · ${escapeHtml(v.nombre)}` +
          `${v.activa ? "" : " (vieja)"}</option>`).join("");
      return ventas;
    })
    .catch(e => {
      sel.innerHTML = `<option value="">No pude leer las campañas</option>`;
      throw e;
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
  selectIntervaloUnidad(document.getElementById("orden-intervalo-unidad").value || "minutos", true);
  document.getElementById("intervalo-input").value = document.getElementById("orden-intervalo-valor").value || "60";
  _syncIntervaloPresets();
  show("intervalo-overlay");
}

function cerrarIntervaloModal() {
  _intervaloPendingBtn = null;
  hide("intervalo-overlay");
}

// Mínimo y valor por defecto de cada unidad. 60 días no es un intervalo que
// alguien quiera: al cambiar de unidad el número tiene que cambiar con ella.
const _INTERVALO_UNIDADES = {
  minutos: { min: 45, def: 60, presets: [45, 60, 120], label: "minutos" },
  dias:    { min: 1,  def: 1,  presets: [1, 2, 7],     label: "días" },
};

function selectIntervaloUnidad(unidad, conservarValor) {
  const meta = _INTERVALO_UNIDADES[unidad] || _INTERVALO_UNIDADES.minutos;
  const input = document.getElementById("intervalo-input");
  const cambio = input.dataset.unidad && input.dataset.unidad !== unidad;

  document.getElementById("intervalo-unidad-minutos").classList.toggle("cuando-pill--active", unidad === "minutos");
  document.getElementById("intervalo-unidad-dias").classList.toggle("cuando-pill--active", unidad === "dias");
  document.getElementById("intervalo-hint").textContent = `Ingresá el intervalo en ${meta.label} (mínimo ${meta.min}).`;
  document.getElementById("intervalo-unidad-txt").textContent = meta.label;
  input.dataset.unidad = unidad;
  input.min = meta.min;
  // Al cambiar de unidad se arranca del valor típico de esa unidad (60 minutos,
  // 1 día); al abrir el modal se respeta lo que ya estaba elegido.
  if (cambio && !conservarValor) input.value = meta.def;

  document.getElementById("intervalo-presets").innerHTML = meta.presets.map(v =>
    `<button type="button" class="intervalo-preset" data-v="${v}" onclick="setIntervaloPreset(${v})">${v} ${meta.label}</button>`
  ).join("");
  _syncIntervaloPresets();

  document.getElementById("intervalo-input-error").classList.add("hidden");
  input.classList.remove("orden-input--error");
}

function setIntervaloPreset(v) {
  const input = document.getElementById("intervalo-input");
  input.value = v;
  clearFieldError("intervalo-input");
  _syncIntervaloPresets();
}

// Marca el atajo que coincide con lo escrito, para que el estado se vea.
function _syncIntervaloPresets() {
  const v = document.getElementById("intervalo-input").value;
  document.querySelectorAll("#intervalo-presets .intervalo-preset").forEach(b => {
    b.classList.toggle("intervalo-preset--on", b.dataset.v === String(v));
  });
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

// ── Órdenes repetidas ────────────────────────────────────────────────────────
// Se mandaban dos veces la misma orden sin que nada avisara (el CRM las junta
// después, pero mientras tanto el gasto se duplica). Dos órdenes son "la misma"
// si coinciden producto, link, cantidad y cuándo salen.
// "2026-08-09T19:30" (input datetime-local) → "2026-08-09 19:30" (formato CRM).
function _fechaCRM(valor) {
  const d = new Date(valor);
  if (isNaN(d)) return "";
  const p = x => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// La cantidad NO entra en la firma: con rangos automáticos cada carga saca un
// número distinto al azar, así que dos órdenes del mismo producto al mismo link
// y momento pasaban como diferentes cuando en realidad son la misma orden
// cargada dos veces. Si hacen falta más unidades, se edita la que ya está.
function _firmaOrden(o) {
  return [o.productoId, (o.link || "").trim().toLowerCase(),
          o.cuando, o.fechaProgramada || ""].join("|");
}

function _avisoDuplicado(msg) {
  const el = document.getElementById("orden-dup-alerta");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
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

  // El número ya está completo: ahora sí se lo lleva al rango del producto
  // (durante la carga no se toca, ver _ajustarCantidadAlRango).
  _ajustarCantidadAlRango();
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

  // ¿Ya hay una igual en la lista? Mismo producto + mismo link + mismo momento
  // ya es la misma orden, sin importar la cantidad.
  const nSplit = cuando === "split3" ? 3 : cuando === "split5" ? 5 : 0;
  const linkNorm = link.toLowerCase();
  const prodId = parseInt(prodSelect.value);
  const prodNombre = prodSelect.options[prodSelect.selectedIndex].text;
  const fechaProgCand = cuando === "programar" && fechaEl.value ? _fechaCRM(fechaEl.value) : "";
  const firmaCand = _firmaOrden({ productoId: prodId, link, cuando, fechaProgramada: fechaProgCand });

  const repetida = nSplit
    // Una tanda partida se compara como el bloque entero: mismo producto, link
    // y misma cantidad de partes. Las fechas no entran porque se calculan desde
    // "ahora" y nunca serían iguales.
    ? ordenes.some(o => o.splitTotal === nSplit && o.productoId === prodId
                     && (o.link || "").trim().toLowerCase() === linkNorm)
    : ordenes.some(o => !o.splitTotal && _firmaOrden(o) === firmaCand);

  // Repetida = no entra. Antes se dejaba pasar con un segundo click, pero una
  // orden duplicada al mismo link y momento siempre fue un error de carga: se
  // paga dos veces lo mismo. Si hacen falta más unidades, se edita la que está.
  if (repetida) {
    _avisoDuplicado(`⛔ Ya hay una orden de "${prodNombre}" para este mismo link y momento. No se puede cargar dos veces lo mismo: editá la que ya está en la lista (✏️) para cambiarle la cantidad, o elegí otro producto, link o momento.`);
    return;
  }
  _avisoDuplicado(null);

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
      fechaProgramada: fechaProgCand,
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
  _lastCantMin = null;
  _lastCantMax = null;

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
  // El form pasa a tener otra orden: el aviso de repetida ya no aplica.
  _avisoDuplicado(null);

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
          <span class="orden-card-pill orden-card-pill--qty">${o.tipo === "comentarios"
            ? `<b>${o.cantidad}</b> comentarios`
            : `<b>${o.cantidad.toLocaleString("es-AR")}</b> unidades`}</span>
          ${o.splitTotal ? `<span class="orden-card-pill orden-card-pill--split">${o.splitIndex}/${o.splitTotal}</span>` : ""}
          ${o.turno ? `<span class="orden-card-pill orden-card-pill--split">⏰ turno ${o.turno}</span>` : ""}
        </div>
        <div class="orden-card-meta">
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
  // Si el "atrás" del navegador está armado, lo consumimos: así el botón y el
  // gesto de atrás dejan el historial en el mismo estado.
  if (history.state?.step === "ordenes") { history.back(); return; }
  _volverAComentariosUI();
}

function _volverAComentariosUI() {
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

  // La orden no salió PERO quedó guardada y un worker la reintenta sola. No es
  // un fallo: si se muestra en rojo, el vendedor la vuelve a cargar a mano y
  // termina duplicada cuando la cola la manda.
  const encolada = !!(rc && rc.encolada);
  const fallo = errores.length > 0 && !encolada;
  // Que el CRM acepte MENOS órdenes de las que mandamos es el caso peligroso:
  // antes se perdía entre los mensajes y el vendedor creía que salió todo.
  const faltan = !fallo && !encolada && insertadas > 0 && insertadas < enviadas;
  const estado = encolada ? { clase: "warn", ico: "⏳", txt: "En cola — se envía sola" }
    : fallo ? { clase: "bad", ico: "✕", txt: "No se pudo enviar" }
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
      <div class="res-bloque-t">${encolada ? "Todavía no salió" : "No se pudo completar"}</div>
      ${errores.map(e => `<div class="res-msg res-msg--bad"><span class="res-msg-ico">${encolada ? "⏳" : "❌"}</span><span class="res-msg-txt">${escapeHtml(e)}</span></div>`).join("")}
      ${_bloqueReintentoCampania()}
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

  // El selector del reintento se puebla después del innerHTML (el <select>
  // recién existe acá).
  const selRetry = document.getElementById("res-retry-select");
  if (selRetry) {
    const exige = !!document.getElementById("res-retry-btn")?.dataset.exigeCampania;
    _llenarSelectVentas(selRetry)
      .then(ventas => {
        if (!ventas.length) return;
        // Cuando cambiar de campaña es opcional, la opción vacía tiene que decir
        // qué pasa si la dejás así: "elegí una" haría pensar que es obligatoria.
        if (!exige) {
          const vacia = selRetry.querySelector('option[value=""]');
          if (vacia) vacia.textContent = "— Con la misma campaña —";
        } else if (window._ventaElegida) {
          selRetry.value = window._ventaElegida;
        }
      })
      .catch(() => {});
  }

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

// Reintentar lo que falló con OTRA campaña, sin rehacer el post.
//
// El caso real: el vendedor eligió una campaña sin crédito (o el cliente no
// tenía ninguna asignada), el CRM rebotó la orden y hasta ahora la única salida
// era volver a generar todos los comentarios desde cero. Acá elige otra campaña
// y se reenvía SOLO la mitad que no entró: si el tráfico ya salió, no se toca.
function _bloqueReintentoCampania() {
  const f = window._envioFallido;
  if (!f) return "";
  const pendientes = f.comentarios.length + f.trafico.length;
  if (!pendientes) return "";

  const que = f.comentarios.length && f.trafico.length ? "la orden"
    : f.comentarios.length ? "los comentarios" : "el tráfico";
  // Cambiar de campaña es OBLIGATORIO solo cuando el CRM no pudo resolver
  // ninguna. En el resto de los fallos (saldo insuficiente, red caída, el CRM
  // que rebota) reintentar con la misma campaña es una opción legítima, así que
  // el selector queda como algo opcional y el botón anda sin tocarlo.
  const hayQueElegir = f.motivo === "sin_campania";
  const titulo = hayQueElegir
    ? `Elegí otra campaña y reintentá ${que}`
    : `Reintentá ${que} sin rehacer el post`;
  const sub = hayQueElegir
    ? "Se reenvía solo lo que no entró."
    : "Se reenvía solo lo que no entró. Si el problema era el saldo, cambiá de campaña antes de reintentar.";
  return `
    <div class="res-retry">
      <div class="res-retry-t">${escapeHtml(titulo)}</div>
      <div class="res-retry-sub">${escapeHtml(sub)}</div>
      <div class="res-retry-row">
        <select id="res-retry-select" class="res-retry-select"></select>
        <button id="res-retry-btn" class="res-retry-btn"
                onclick="reintentarConOtraCampania()"
                data-exige-campania="${hayQueElegir ? "1" : ""}">Reintentar</button>
      </div>
      <div id="res-retry-msg" class="res-retry-msg hidden"></div>
    </div>`;
}

function _resRetryMsg(texto) {
  const m = document.getElementById("res-retry-msg");
  if (!m) return;
  m.textContent = texto || "";
  m.classList.toggle("hidden", !texto);
}

async function reintentarConOtraCampania() {
  const f = window._envioFallido;
  const sel = document.getElementById("res-retry-select");
  const btn = document.getElementById("res-retry-btn");
  if (!f || !sel || !btn) return;

  const idventa = sel.value || "";
  if (!idventa && btn.dataset.exigeCampania) {
    _resRetryMsg("Elegí una campaña de la lista.");
    return;
  }
  const saldo = _ventasSaldo[String(idventa)];
  if (idventa && saldo !== undefined && saldo <= 0) {
    // Es exactamente el error del que venimos: mandar a una campaña sin crédito
    // vuelve a rebotar. Se avisa antes de gastar el viaje al CRM.
    _resRetryMsg("Esa campaña tampoco tiene saldo: elegí otra.");
    return;
  }

  // Sin elegir nada se reintenta con la misma campaña de antes (o con la que el
  // backend resuelva sola si el post tiene cliente asignado).
  if (idventa) window._ventaElegida = idventa;
  btn.disabled = true;
  sel.disabled = true;
  btn.textContent = "Reintentando…";
  _resRetryMsg("");

  // Se reintenta solo lo pendiente. `_ejecutarEnvio` recalcula `_envioFallido`,
  // así que si vuelve a fallar el bloque de reintento aparece de nuevo.
  const comentarios = f.comentarios;
  const trafico = f.trafico;
  try {
    const data = await _ejecutarEnvio(comentarios, trafico);
    renderResultado(data, comentarios.length, trafico.length);
  } catch (e) {
    btn.disabled = false;
    sel.disabled = false;
    btn.textContent = "Reintentar";
    _resRetryMsg("No pudimos reintentar: " + (e.message || String(e)));
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
      : `${Number(o.cantidad || 0).toLocaleString("es-AR")} unidades`;
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

// De qué post, de qué cliente y de qué campaña es CUALQUIER envío al CRM.
//
// Existe para que no se pueda volver a repetir el bug que dejó a Pedro sin
// comentarios: el envío de tráfico mandaba `client` e `idventa` y el de
// comentarios no, así que el backend no podía resolver la campaña y rebotaba
// los comentarios mientras el tráfico del mismo post entraba sin problema.
// Los dos envíos arrancan de acá: si mañana se agrega un tercero, hereda esto
// gratis en vez de tener que acordarse.
//
// `idventa` solo va cuando el post no es de un cliente; el backend la valida
// contra las campañas de la cuenta antes de usarla.
function _contextoDeEnvio() {
  return {
    url: currentUrl,
    client: window._clientIg || "",
    idventa: window._ventaElegida || "",
  };
}

// Comentarios: publicar en Instagram vía IA. Puede haber 2 órdenes
// (verificados 94 + no verificados 95), cada una con su propia lista.
async function _enviarComentariosAlCrm(ordenesComentarios) {
  const resp = await fetch("/api/publicar", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ..._contextoDeEnvio(),
      // top-level: unión de todas (para el informe / fallback)
      comentarios: comentariosParaPublicar,
      ordenes: ordenesComentarios.map(o => ({
        ...o,
        // cantidad = comentarios reales (sin los encabezados hombres:/mujeres:)
        cantidad: (o.comentarios || []).filter(c => !generoDeHeader(c)).length,
      })),
    }),
  });
  return await resp.json();
}

// Followers / likes / etc: enviar al CRM.
async function _enviarTraficoAlCrm(ordenesNormales) {
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
      ..._contextoDeEnvio(),
      ordenes: crmOrdenes,
      costo_total: costoTotal,
    }),
  });
  return await traficoResp.json().catch(() => ({}));
}

// Manda las dos mitades del envío y arma el `data` que lee renderResultado.
// Está separado de solicitarOrdenes porque el botón de "reintentar con otra
// campaña" lo vuelve a llamar con SOLO lo que falló: los comentarios ya
// generados no se rehacen y el tráfico que ya entró no se duplica.
async function _ejecutarEnvio(ordenesComentarios, ordenesNormales) {
  let data = {};

  if (ordenesComentarios.length > 0 && comentariosParaPublicar.length > 0) {
    data = await _enviarComentariosAlCrm(ordenesComentarios);
  }

  if (ordenesNormales.length > 0) {
    const traficoData = await _enviarTraficoAlCrm(ordenesNormales);
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

  // Qué mitad quedó sin entrar: es lo único que se reintenta después.
  //
  // Los comentarios pueden fallar de dos formas: con `resultado` (el CRM
  // contestó y rebotó) o con un `error` pelado y sin `resultado` (el backend
  // cortó antes, ej. faltan datos). Las dos cuentan como no entró.
  //
  // La orden ENCOLADA no es un fallo: un worker la reintenta sola, y ofrecer
  // reintentar ahí la duplica.
  const huboComentarios = ordenesComentarios.length > 0 && comentariosParaPublicar.length > 0;
  const encolada = !!(data.resultado && data.resultado.encolada);
  const falloComentarios = huboComentarios && !encolada && (
    data.resultado ? !data.resultado.ok : !!data.error
  );
  const falloTrafico = !!(data.trafico && (data.trafico.error || data.trafico.errors?.length));
  window._envioFallido = {
    comentarios: falloComentarios ? ordenesComentarios : [],
    trafico:     falloTrafico ? ordenesNormales : [],
    motivo: (falloComentarios && data.resultado && data.resultado.motivo)
         || (falloTrafico && data.trafico.motivo) || null,
  };
  return data;
}

async function solicitarOrdenes() {
  if (ordenes.length === 0) return;

  // Post sin cliente: no mandamos nada hasta saber de qué campaña sale la orden.
  // Vale también para los comentarios: aunque no toquen el saldo, el CRM los
  // carga contra una campaña igual que al tráfico, y sin ella los rebota.
  if (!window._clienteAsignado && !window._ventaElegida) {
    const box = document.getElementById("venta-picker");
    box.classList.remove("hidden");
    box.classList.add("venta-picker--falta");
    _ventaPickerMsg("Elegí una campaña para poder enviar la orden.", true);
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
    const data = await _ejecutarEnvio(ordenesComentarios, ordenesNormales);
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
  cargarEnviosFallidos();
});


// ── Órdenes que no entraron al CRM ───────────────────────────────────────────
// En Growi estas órdenes no aparecen: o el CRM las rechazó, o ni llegaron a
// salir (la cuenta sin campaña activa, sin idvendedor, el proxy caído). El
// vendedor veía el error una sola vez, en el momento, y después no le quedaba
// ningún lado donde mirarlas. Esto es ese lado.

let _enviosFallidos = [];

async function cargarEnviosFallidos() {
  const btn = document.getElementById("btn-fallidos");
  if (!btn) return;
  try {
    const r = await fetch("/api/mis-envios-fallidos");
    if (!r.ok) return;                       // sin DB o sin login: el botón queda oculto
    const d = await r.json();
    _enviosFallidos = d.envios || [];
  } catch (e) {
    return;                                  // esto es informativo: nunca molesta al vendedor
  }
  btn.classList.toggle("hidden", _enviosFallidos.length === 0);
  document.getElementById("btn-fallidos-count").textContent = _enviosFallidos.length;
}

function abrirEnviosFallidos() {
  const cont = document.getElementById("fallidos-lista");
  cont.innerHTML = "";

  if (!_enviosFallidos.length) {
    const p = document.createElement("p");
    p.className = "intervalo-subtitle";
    p.textContent = "No hay órdenes rebotadas. Todo lo que mandaste entró.";
    cont.appendChild(p);
  }

  for (const e of _enviosFallidos) {
    const item = document.createElement("div");
    item.className = "fallido-item";

    const head = document.createElement("div");
    head.className = "fallido-head";
    const motivo = document.createElement("span");
    motivo.className = `fallido-motivo fallido-motivo--${e.motivo}`;
    motivo.textContent = e.detalle;
    head.appendChild(motivo);
    const fecha = document.createElement("span");
    fecha.className = "fallido-fecha";
    fecha.textContent = e.fecha ? _labelFecha(new Date(e.fecha)) : "—";
    head.appendChild(fecha);
    item.appendChild(head);

    const meta = document.createElement("p");
    meta.className = "fallido-meta";
    const partes = [];
    if (e.cliente) partes.push(`@${e.cliente}`);
    if (e.costo) partes.push(`$${e.costo}`);
    if (e.tipo === "cola") partes.push("quedó en la cola");
    meta.textContent = partes.join(" · ") || "—";
    item.appendChild(meta);

    if (e.post_url) {
      const link = document.createElement("a");
      link.className = "fallido-link";
      link.href = e.post_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = e.post_url;
      item.appendChild(link);
    }

    // El texto crudo del error va escondido: al vendedor no le dice nada, pero
    // es lo primero que pide el admin cuando le reenvían la captura.
    if (e.tecnico) {
      const det = document.createElement("details");
      det.className = "fallido-tecnico";
      const sum = document.createElement("summary");
      sum.textContent = "Detalle técnico";
      det.appendChild(sum);
      const pre = document.createElement("p");
      pre.textContent = e.tecnico;
      det.appendChild(pre);
      item.appendChild(det);
    }

    // Reintentar solo lo que la cola puede reenviar sola: de esas órdenes está
    // guardado el payload entero. De un envío rebotado quedó la traza pero no
    // los comentarios, así que ahí no hay nada que reenviar.
    if (e.reintentable) {
      const acciones = document.createElement("div");
      acciones.className = "fallido-acciones";
      const btn = document.createElement("button");
      btn.className = "fallido-btn";
      btn.textContent = "Reintentar";
      btn.onclick = () => reintentarOrdenEncolada(e, btn);
      acciones.appendChild(btn);
      if (e.aviso_duplicado) {
        const nota = document.createElement("span");
        nota.className = "fallido-nota";
        nota.textContent = "Pudo haber entrado: revisá en Growi antes";
        acciones.appendChild(nota);
      }
      item.appendChild(acciones);
    }

    cont.appendChild(item);
  }
  show("fallidos-overlay");
}

// El reintento no manda nada desde acá: devuelve la orden a la cola y la
// despacha el worker, que es el único con el claim atómico contra el envío
// duplicado. Por eso el mensaje dice "va a salir sola" y no "enviada".
async function reintentarOrdenEncolada(envio, btn) {
  if (envio.aviso_duplicado &&
      !confirm("Esta orden pudo haber entrado al CRM: si entró y la reenviás, " +
               "se le cobra dos veces al cliente.\n\n" +
               "¿Ya revisaste en Growi que no esté cargada?")) return;

  btn.disabled = true;
  btn.textContent = "Reintentando…";
  try {
    const r = await fetch(`/api/ordenes-pendientes/${envio.id}/reintentar`,
                          { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || "No se pudo reintentar");
    btn.textContent = "✓ Vuelve a la cola";
    btn.classList.add("fallido-btn--ok");
    // Sale del listado: ya no es un fallo, es una orden esperando salir.
    _enviosFallidos = _enviosFallidos.filter(x => !(x.tipo === "cola" && x.id === envio.id));
    const badge = document.getElementById("btn-fallidos-count");
    if (badge) badge.textContent = _enviosFallidos.length;
    document.getElementById("btn-fallidos")
            .classList.toggle("hidden", _enviosFallidos.length === 0);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Reintentar";
    const nota = document.createElement("span");
    nota.className = "fallido-nota fallido-nota--error";
    nota.textContent = err.message;
    btn.parentElement.appendChild(nota);
  }
}

function cerrarEnviosFallidos() {
  hide("fallidos-overlay");
}
