let currentUrl = "";
let comentariosGenerados = [];
let currentJobId = null;
let streamOffset = 0;
let streamProgresoOffset = 0;
let streamMeta = {};

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
  const status = document.getElementById("stream-status");
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
          const evento = JSON.parse(line.slice(6));
          manejarEvento(evento);
        }
      }
    } catch (e) {
      // Conexión cortada — reconectar tras 1.5s desde donde quedó
      setProgreso("Reconectando...");
      setTimeout(conectarStream, 1500);
    }
  }).catch(() => {
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
  } else if (evento.tipo === "comentario") {
    agregarComentario(evento.texto, evento.index);
    streamOffset = evento.index + 1;
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

function mostrarScrape(data) {
  document.getElementById("scrape-owner").textContent = data.owner_username || "—";
  document.getElementById("client-badge").textContent = data.client_id || "Sin cliente detectado";

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
    <input type="checkbox" id="chk-${i}" onchange="actualizarConteo()" />
    <label class="comentario-texto" for="chk-${i}">${escapeHtml(texto)}</label>
  `;
  item.addEventListener("click", (e) => {
    if (e.target.tagName === "INPUT") return;
    const chk = item.querySelector("input");
    chk.checked = !chk.checked;
    item.classList.toggle("selected", chk.checked);
    actualizarConteo();
  });
  item.querySelector("input").addEventListener("change", () => {
    item.classList.toggle("selected", item.querySelector("input").checked);
  });
  // Sacar skeleton al primer comentario
  const skeleton = document.getElementById("skeleton-list");
  if (skeleton) skeleton.remove();

  lista.appendChild(item);
  actualizarConteo();
  document.getElementById("comments-actions-bar").classList.remove("hidden");
}

function finalizarStream(meta) {
  mostrarScrape(meta);
  hide("loading-overlay");
  document.getElementById("stream-status").textContent = "";
  document.getElementById("status-listo").classList.remove("hidden");
  document.getElementById("btn-publicar").disabled = false;
}


function actualizarConteo() {
  const checks = document.querySelectorAll("#lista-comentarios input[type=checkbox]");
  const seleccionados = [...checks].filter((c) => c.checked).length;
  document.getElementById("count-label").textContent = `${seleccionados} seleccionados`;
  document.getElementById("btn-publicar").disabled = seleccionados === 0;
}

function seleccionarTodos() {
  document.querySelectorAll("#lista-comentarios input[type=checkbox]").forEach((c) => {
    c.checked = true;
    c.closest(".comentario-item").classList.add("selected");
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

async function publicar() {
  const checks = document.querySelectorAll("#lista-comentarios input[type=checkbox]");
  const seleccionados = comentariosGenerados.filter((_, i) => checks[i]?.checked);

  if (seleccionados.length === 0) return;

  document.getElementById("btn-publicar").disabled = true;
  document.getElementById("btn-publicar").textContent = "Publicando...";

  try {
    const resp = await fetch("/api/publicar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, comentarios: seleccionados }),
    });

    const data = await resp.json();

    hide("step-comentarios");
    const box = document.getElementById("resultado-content");
    if (data.error) {
      box.textContent = `Error: ${data.error}`;
    } else {
      box.textContent = data.informe || JSON.stringify(data, null, 2);
    }
    show("step-resultado");
  } catch (e) {
    alert("Error al publicar: " + e.message);
    document.getElementById("btn-publicar").disabled = false;
    document.getElementById("btn-publicar").textContent = "Publicar seleccionados";
  }
}

function reiniciar() {
  currentUrl = "";
  comentariosGenerados = [];
  currentJobId = null;
  streamOffset = 0;
  streamProgresoOffset = 0;
  streamMeta = {};
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

  hide("step-loading");
  hide("step-comentarios");
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

// Enter key en el input
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("ig-link").addEventListener("keydown", (e) => {
    if (e.key === "Enter") generarComentarios();
  });
  actualizarConteo();
});
