let currentUrl = "";
let comentariosGenerados = [];
let currentJobId = null;
let streamOffset = 0;
let streamProgresoOffset = 0;
let streamMeta = {};
let esperandoTranscripcion = false;
let pendingComentarios = [];

function show(id) {
  document.getElementById(id).classList.remove("hidden");
}

function hide(id) {
  document.getElementById(id).classList.add("hidden");
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

async function generarComentarios() {
  const url = document.getElementById("ig-link").value.trim();
  if (!url) {
    setError("Pegá un link de Instagram primero.");
    return;
  }

  setError("");
  currentUrl = url;
  currentJobId = null;
  streamOffset = 0;
  streamProgresoOffset = 0;
  streamMeta = {};
  comentariosGenerados = [];
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
      body: JSON.stringify({ url }),
    });
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    currentJobId = data.job_id;
  } catch (e) {
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
    sk.innerHTML = '<div class="skeleton-item"></div>'.repeat(5);
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
  if (!currentJobId) return;

  const url = `/api/stream/${currentJobId}?offset=${streamOffset}&progreso_offset=${streamProgresoOffset}`;
  const reader = fetch(url).then((r) => r.body.getReader());

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
      console.error("[stream] catch error:", e);
      setProgreso("Reconectando...");
      setTimeout(conectarStream, 1500);
    }
  }).catch((e) => {
    console.error("[stream] fetch catch:", e);
    setProgreso("Reconectando...");
    setTimeout(conectarStream, 1500);
  });
}

function manejarEvento(evento) {
  if (evento.tipo === "progreso") {
    setProgreso(evento.mensaje);
    streamProgresoOffset++;
  } else if (evento.tipo === "scrape") {
    mostrarScrape(evento);
  } else if (evento.tipo === "step") {
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
    if (evento.texto && !evento.texto.startsWith("(")) {
      document.getElementById("transcription-text").textContent = evento.texto;
      document.getElementById("transcription-block").classList.remove("hidden");
    }
    pendingComentarios.forEach((e) => {
      ocultarChunk();
      agregarComentario(e.texto, e.index);
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
      agregarComentario(evento.texto, evento.index);
    }
  } else if (evento.tipo === "listo") {
    streamMeta = evento;
    finalizarStream(evento);
  } else if (evento.tipo === "error") {
    hide("loading-overlay");
    hide("step-comentarios");
    show("step-input");
    setError(evento.mensaje);
  }
}

const STEP_LABELS = {
  transcription: { icon: "🎙️", texto: "Generando la transcripción del video...", sub: "menos de 60 segundos" },
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

function mostrarChunk(texto) {
  const el = document.getElementById("typing-preview");
  el.textContent = texto;
  el.classList.remove("hidden");
  const skeleton = document.getElementById("skeleton-list");
  if (skeleton) skeleton.remove();
}

function ocultarChunk() {
  const el = document.getElementById("typing-preview");
  el.textContent = "";
  el.classList.add("hidden");
}

function mostrarScrape(data) {
  document.getElementById("scrape-owner").textContent = data.owner_username || "—";
  document.getElementById("client-badge").textContent = data.client_id || "Peter Fournier";

  hide("loading-overlay");

  if (data.caption) {
    document.getElementById("scrape-caption-text").textContent = data.caption;
    document.getElementById("scrape-caption-block").classList.remove("hidden");
  }
  if (data.photo_description) {
    document.getElementById("photo-description-text").textContent = data.photo_description;
    document.getElementById("photo-description-block").classList.remove("hidden");
  }
  if (data.transcription && !data.transcription.startsWith("(")) {
    document.getElementById("transcription-text").textContent = data.transcription;
    document.getElementById("transcription-block").classList.remove("hidden");
  }
}

function agregarComentario(texto, index) {
  const lista = document.getElementById("lista-comentarios");
  const i = index;
  comentariosGenerados[i] = texto;

  const item = document.createElement("div");
  item.className = "comentario-item";
  item.dataset.index = i;
  item.innerHTML = `
    <span class="comentario-num">${i + 1}</span>
    <input type="checkbox" id="chk-${i}" onchange="actualizarConteo()" />
    <label class="comentario-texto">${escapeHtml(texto)}</label>
  `;
  item.addEventListener("click", (e) => {
    if (e.target.tagName === "INPUT") return;
    const chk = item.querySelector("input");
    if (!chk.checked && contarSeleccionados() >= 20) return;
    chk.checked = !chk.checked;
    item.classList.toggle("selected", chk.checked);
    actualizarConteo();
  });
  item.querySelector("input").addEventListener("change", () => {
    const chk = item.querySelector("input");
    if (chk.checked && contarSeleccionados() > 20) { chk.checked = false; return; }
    item.classList.toggle("selected", chk.checked);
    actualizarConteo();
  });
  const skeleton = document.getElementById("skeleton-list");
  if (skeleton) skeleton.remove();

  lista.appendChild(item);

  const counter = document.getElementById("comments-counter");
  const count = lista.querySelectorAll(".comentario-item").length;
  if (counter) {
    counter.textContent = `${count} / 60`;
  }

  actualizarConteo();
  document.getElementById("comments-actions-bar").classList.remove("hidden");
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
  mostrarScrape(meta);
  esperandoTranscripcion = false;
  if (pendingComentarios.length > 0) {
    pendingComentarios.forEach((e) => { ocultarChunk(); agregarComentario(e.texto, e.index); });
    pendingComentarios = [];
  }
  hide("loading-overlay");
  document.getElementById("stream-status").textContent = "";
  document.getElementById("status-listo").classList.remove("hidden");
  document.getElementById("btn-publicar").disabled = false;
  const btnCargar = document.getElementById("btn-cargar-mas");
  if (btnCargar) btnCargar.classList.remove("hidden");
}

async function cargarMas() {
  const btn = document.getElementById("btn-cargar-mas");
  btn.disabled = true;
  btn.textContent = "Cargando...";

  try {
    const resp = await fetch("/api/procesar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl }),
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

    const url = `/api/stream/${currentJobId}?offset=0&progreso_offset=0`;
    const resp2 = await fetch(url);
    const reader = resp2.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
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
          agregarComentario(evento.texto, baseIndex + evento.index);
        } else if (evento.tipo === "listo" || evento.tipo === "error") {
          break;
        }
      }
    }
  } catch (e) {
    console.error("cargarMas error:", e);
  }

  btn.disabled = false;
  btn.textContent = "+ Cargar más";
}



function contarSeleccionados() {
  return [...document.querySelectorAll("#lista-comentarios input[type=checkbox]")].filter(c => c.checked).length;
}

function actualizarConteo() {
  const sel = contarSeleccionados();
  const label = document.getElementById("count-label");
  if (label) {
    label.textContent = `${sel} / 20`;
    label.classList.toggle("count-label--lleno", sel >= 20);
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
  count.textContent = `${seleccionados.length} / 20`;

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
  let count = 0;
  document.querySelectorAll("#lista-comentarios input[type=checkbox]").forEach((c) => {
    if (count < 20) { c.checked = true; c.closest(".comentario-item").classList.add("selected"); count++; }
  });
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

async function irAOrdenes() {
  const checks = document.querySelectorAll("#lista-comentarios input[type=checkbox]");
  const seleccionados = comentariosGenerados.filter((_, i) => checks[i]?.checked);
  if (seleccionados.length === 0) return;

  comentariosParaPublicar = seleccionados;
  ordenes = [];

  // Info del post
  const clientName = document.getElementById("client-badge").textContent || "—";
  document.getElementById("ordenes-post-url").textContent = currentUrl;
  document.getElementById("ordenes-client").textContent = clientName;
  document.getElementById("ordenes-hero-avatar").textContent = clientName.charAt(0).toUpperCase();
  document.getElementById("orden-link").value = currentUrl;

  // Orden pre-cargada de comentarios
  ordenes.push({
    id: Date.now(),
    redsocial: "Instagram",
    redsocialId: "1",
    productoId: 94,
    productoNombre: "Comentarios Reales Verificados",
    cantidad: seleccionados.length,
    link: currentUrl,
    cuando: "ahora",
    cuandoLabel: "Ahora",
    obs: "",
    tipo: "comentarios",
  });

  await actualizarProductos();
  renderOrdenes();

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
  obtenerCosto();
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
  if (!cantidad || cantidad < 1) {
    setFieldError("orden-cantidad", "Ingresá una cantidad válida");
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

  lista.innerHTML = ordenes.map((o, i) => {
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
        </div>
        <div class="orden-card-meta">
          <span class="orden-card-pill orden-card-pill--qty">${o.tipo === "comentarios" ? `${o.cantidad} comentarios` : `${o.cantidad.toLocaleString()} uds`}</span>
          <span class="orden-card-pill orden-card-pill--when">${o.splitTotal ? "⏰" : (CUANDO_ICONS[o.cuando] || "⚡")} ${escapeHtml(o.cuandoLabel)}</span>
          ${o.costo != null && o.costo > 0 ? `<span class="orden-card-pill orden-card-pill--cost">$${parseFloat(o.costo).toFixed(4)}</span>` : ""}
          ${o.tipo === "comentarios" ? `<span class="orden-card-pill orden-card-pill--green">✓ Comentarios IA</span>` : ""}
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

async function solicitarOrdenes() {
  if (ordenes.length === 0) return;

  const btn = document.getElementById("btn-solicitar");
  btn.disabled = true;
  btn.textContent = "Solicitando...";

  const ordenComentarios = ordenes.find(o => o.tipo === "comentarios");
  const ordenesNormales  = ordenes.filter(o => o.tipo !== "comentarios");

  try {
    let data = {};

    // Comentarios: publicar en Instagram vía IA
    if (ordenComentarios && comentariosParaPublicar.length > 0) {
      const resp = await fetch("/api/publicar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: currentUrl,
          comentarios: comentariosParaPublicar,
          ordenes: [{ ...ordenComentarios, cantidad: comentariosParaPublicar.length }],
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
        body: JSON.stringify({ ordenes: crmOrdenes, costo_total: costoTotal }),
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
    }

    hide("step-ordenes");

    const box = document.getElementById("resultado-content");
    if (data.error) {
      box.innerHTML = `<span style="color:#e55;">Error: ${escapeHtml(data.error)}</span>`;
    } else {
      box.innerHTML = `
        <div style="margin-bottom:10px;white-space:pre-line;">${escapeHtml(data.informe || "Órdenes enviadas correctamente.")}</div>
        <div style="color:var(--muted2);font-size:0.82rem;">
          ${ordenes.length} orden${ordenes.length > 1 ? "es" : ""} procesada${ordenes.length > 1 ? "s" : ""}
        </div>
      `;
    }

    if (ordenComentarios) {
      const resultadoComments = document.getElementById("resultado-comments");
      resultadoComments.innerHTML = comentariosParaPublicar.map((c, i) =>
        `<div class="resultado-comment-item"><span class="resultado-comment-num">${i + 1}</span><span class="resultado-comment-texto">${escapeHtml(c)}</span></div>`
      ).join("");
    }

    show("step-resultado");
  } catch (e) {
    alert("Error al solicitar: " + e.message);
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
  document.getElementById("lista-comentarios").innerHTML = "";
  document.getElementById("status-listo").classList.add("hidden");
  document.getElementById("scrape-owner").textContent = "—";
  document.getElementById("client-badge").textContent = "—";
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

function hacerPrueba() {
  document.getElementById("ig-link").value = "https://www.instagram.com/p/DZyYdR9xMLi/";
  generarComentarios();
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

// Enter key en el input
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("ig-link").addEventListener("keydown", (e) => {
    if (e.key === "Enter") generarComentarios();
  });
  actualizarConteo();
});
