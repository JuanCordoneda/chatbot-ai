// Envío de followers: link del perfil + calidad del catálogo del CRM + cantidad.
// Reusa los mismos endpoints que Generar órdenes (/api/productos, /api/costo_trafico,
// /api/enviar_trafico), así el costo, los fondos y el registro de uso son idénticos.

// ── Tema (mismo contrato que app.js: clase .light en body + localStorage) ──
function toggleTheme() {
  const isLight = document.body.classList.toggle("light");
  localStorage.setItem("theme", isLight ? "light" : "dark");
  document.getElementById("btn-theme").textContent = isLight ? "☾ Dark" : "☀ Light";
}
(function () {
  if (localStorage.getItem("theme") === "light") document.body.classList.add("light");
  document.addEventListener("DOMContentLoaded", () => {
    if (document.body.classList.contains("light"))
      document.getElementById("btn-theme").textContent = "☾ Dark";
  });
})();

// ── Helpers ──
const RS_ID = "1";            // Instagram: followers solo aplica acá
const RS_NOMBRE = "Instagram";

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function plata(n) { return `$${(parseFloat(n) || 0).toFixed(2)}`; }

let toastTimer = null;
function toast(msg) {
  const t = $("copy-toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2400);
}

function showError(msg) { const e = $("fw-error"); e.textContent = msg; e.classList.remove("hidden"); }
function hideError() { $("fw-error").classList.add("hidden"); }
function hint(id, msg, malo) {
  const e = $(id);
  if (!e) return;
  e.textContent = msg || "";
  e.classList.toggle("hidden", !msg);
  e.style.color = malo ? "#ff6b6b" : "";
}

function igUsernameDe(texto) {
  const t = (texto || "").trim();
  if (!t) return "";
  const m = t.match(/instagram\.com\/([A-Za-z0-9._]+)/i);
  if (m) {
    const u = m[1].toLowerCase();
    return ["p", "reel", "reels", "tv", "stories", "explore"].includes(u) ? "" : u;
  }
  if (t.toLowerCase().includes("instagram.com")) return "";
  return t.replace(/^@/, "").replace(/[^A-Za-z0-9._]/g, "").toLowerCase();
}

// ── Clientes (definen de qué campaña salen los fondos) ──
let clientes = [];

async function cargarClientes() {
  const sel = $("fw-cliente");
  try {
    const r = await fetch("/api/followers/clientes");
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "No pude traer los clientes");
    clientes = d.clients || [];
  } catch (e) {
    sel.innerHTML = '<option value="">No pude cargar los clientes</option>';
    return;
  }
  sel.innerHTML = '<option value="" selected>— Ninguno (uso el link) —</option>' +
    clientes.map(c => {
      // El admin ve clientes de todos los vendedores: sin el nombre del vendedor
      // dos clientes homónimos de cuentas distintas son indistinguibles.
      const v = c.vendedor ? ` — ${esc(c.vendedor)}` : "";
      return `<option value="${c.id}">${esc(c.display_name || c.ig_username)} · @${esc(c.ig_username)}${v}</option>`;
    }).join("");
}

function clienteElegido() {
  const id = parseInt($("fw-cliente").value || "0");
  if (id) return clientes.find(c => c.id === id) || null;
  // Sin selección manda el link: si es de un cliente cargado, vale igual.
  const u = igUsernameDe($("fw-link").value);
  if (!u) return null;
  // Un mismo @usuario puede estar cargado en más de un vendedor (el admin los ve
  // todos): gana el activo.
  const match = clientes.filter(c => c.ig_username === u);
  return match.find(c => c.status === "active") || match[0] || null;
}

function onClienteChange() {
  const c = clienteElegido();
  if (c) $("fw-link").value = "https://www.instagram.com/" + c.ig_username;
  $("fw-link-error").classList.add("hidden");
  hideError();
  revisarPerfil();
}

function onLinkInput() {
  const c = clientes.find(x => x.id === parseInt($("fw-cliente").value || "0"));
  if (c && !$("fw-link").value.toLowerCase().includes(c.ig_username)) $("fw-cliente").value = "";
  $("fw-link-error").classList.add("hidden");
  hideError();
  refrescarClearPerfil();
  validarPerfilVivo();
  revisarPerfil();
}

// ── Campo del perfil: pegar, limpiar, validar ─────────────────────────────

// Verde cuando de lo escrito sale un usuario; ámbar cuando no (típico: se pegó
// el link de un post en vez del del perfil). No bloquea el botón: la validación
// dura sigue estando en pedirFollowers().
function validarPerfilVivo() {
  const wrap = $("fw-link-wrap");
  if (!wrap) return;
  const txt = $("fw-link").value.trim();
  const u = igUsernameDe(txt);
  wrap.classList.toggle("ig-field--ok", !!u);
  wrap.classList.toggle("ig-field--warn", !!txt && !u);
}

function refrescarClearPerfil() {
  const btn = $("fw-clear");
  if (btn) btn.classList.toggle("hidden", $("fw-link").value.trim() === "");
}

function limpiarPerfil() {
  const input = $("fw-link");
  input.value = "";
  onLinkInput();
  input.focus();
}

// El navegador puede negar el portapapeles (permiso, http): no rompemos nada,
// sólo dejamos el foco en el campo para pegar a mano.
async function pegarPerfil() {
  const input = $("fw-link");
  try {
    const texto = (await navigator.clipboard.readText()).trim();
    if (texto) input.value = texto;
  } catch (e) {
    /* sin permiso de portapapeles */
  }
  onLinkInput();
  input.focus();
}

function setCantidad(n) {
  $("fw-cantidad").value = n;
  onCantidadInput();
}

// ── Perfiles recientes ────────────────────────────────────────────────────
// Al mismo perfil se le manda tráfico en varias tandas; volver a buscar el link
// cada vez es el paso con más fricción.
const FW_RECIENTES_KEY = "growi_perfiles_recientes";
const FW_RECIENTES_MAX = 5;

function leerPerfilesRecientes() {
  try {
    const raw = JSON.parse(localStorage.getItem(FW_RECIENTES_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter(u => typeof u === "string") : [];
  } catch (e) {
    return [];
  }
}

function guardarPerfilReciente(usuario) {
  try {
    const lista = [usuario, ...leerPerfilesRecientes().filter(u => u !== usuario)].slice(0, FW_RECIENTES_MAX);
    localStorage.setItem(FW_RECIENTES_KEY, JSON.stringify(lista));
  } catch (e) {
    /* localStorage lleno o bloqueado: los recientes son un extra */
  }
}

function usarPerfilReciente(usuario) {
  $("fw-link").value = "https://www.instagram.com/" + usuario;
  $("fw-cliente").value = "";
  onLinkInput();
  $("fw-link").focus();
}

function pintarPerfilesRecientes() {
  const wrap = $("fw-recientes");
  if (!wrap) return;
  const lista = leerPerfilesRecientes();
  wrap.innerHTML = "";
  wrap.classList.toggle("hidden", lista.length === 0);
  if (!lista.length) return;

  const titulo = document.createElement("span");
  titulo.className = "ig-recientes-label";
  titulo.textContent = "Recientes";
  wrap.appendChild(titulo);

  lista.forEach(u => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ig-reciente";
    chip.title = "@" + u;
    chip.textContent = "@" + u;
    chip.onclick = () => usarPerfilReciente(u);
    wrap.appendChild(chip);
  });
}

// El perfil decide de dónde sale la plata: si es cliente, el backend resuelve su
// campaña solo; si no, hay que elegirla a mano (igual que en Generar órdenes).
function revisarPerfil() {
  const u = igUsernameDe($("fw-link").value);
  const c = clienteElegido();
  if (c) {
    hint("fw-cliente-hint", "Cliente tuyo: la orden se descuenta de su campaña.");
    $("fw-campana-row").classList.add("hidden");
  } else {
    hint("fw-cliente-hint", u ? `@${u} no es un cliente cargado.` : "");
    $("fw-campana-row").classList.toggle("hidden", !u);
    if (u) cargarCampanas();
  }
}

// ── Campañas ──
let campanasCargadas = false;
let saldoPorVenta = {};

async function cargarCampanas() {
  if (campanasCargadas) return;
  campanasCargadas = true;
  const sel = $("fw-campana");
  try {
    const r = await fetch("/api/ventas");
    const d = await r.json();
    const ventas = d.ventas || [];
    if (!ventas.length) {
      sel.innerHTML = '<option value="">No hay campañas disponibles</option>';
      hint("fw-campana-hint", d.error || "No encontramos campañas en tu cuenta del CRM.", true);
      return;
    }
    saldoPorVenta = {};
    ventas.forEach(v => { saldoPorVenta[String(v.idventa)] = parseFloat(v.disponible) || 0; });
    sel.innerHTML = '<option value="">— Elegí una campaña —</option>' +
      ventas.map(v => `<option value="${esc(v.idventa)}">#${esc(v.idventa)} · ${plata(v.disponible)} · ${esc(v.nombre)}${v.activa ? "" : " (vieja)"}</option>`).join("");
  } catch (e) {
    campanasCargadas = false;
    sel.innerHTML = '<option value="">No pude leer las campañas</option>';
    hint("fw-campana-hint", "No pudimos leer tus campañas del CRM.", true);
  }
}

function onCampanaChange() {
  const v = $("fw-campana").value;
  if (!v) { hint("fw-campana-hint", ""); return; }
  const disp = saldoPorVenta[String(v)];
  hint("fw-campana-hint", `Disponible ${plata(disp)}` + (disp <= 0 ? " — sin saldo, el envío va a fallar" : ""), disp <= 0);
}

// ── Calidades (los productos "Followers" del catálogo del CRM) ──
let calidades = [];

async function cargarCalidades() {
  const sel = $("fw-calidad");
  try {
    const r = await fetch(`/api/productos?rrss=${RS_ID}`);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    // El catálogo viene agrupado por familia; nos quedamos con la de followers.
    const grupo = Object.keys(d).find(k => /follower|seguidor/i.test(k));
    calidades = grupo ? d[grupo].map(p => ({ id: String(p.id), nombre: p.label })) : [];
    if (!calidades.length) throw new Error("el CRM no ofrece followers para Instagram");
    sel.innerHTML = '<option value="" disabled selected>Seleccione</option>' +
      calidades.map(c => `<option value="${esc(c.id)}">${esc(c.nombre)}</option>`).join("");
  } catch (e) {
    sel.innerHTML = '<option value="" disabled selected>Error al cargar</option>';
    showError("No pude traer las calidades del CRM: " + e.message);
  }
}

function calidadElegida() {
  return calidades.find(c => c.id === $("fw-calidad").value) || null;
}

function onCalidadChange() {
  $("fw-calidad-error").classList.add("hidden");
  hideError();
  const c = calidadElegida();
  if (c) obtenerDemora(c.nombre);
  obtenerCosto();
}

function onCantidadInput() {
  $("fw-cantidad-error").classList.add("hidden");
  obtenerCosto();
}

async function obtenerDemora(nombre) {
  const badge = $("fw-demora-badge");
  badge.classList.add("hidden");
  try {
    const r = await fetch(`/api/demora?redsocial=${encodeURIComponent(RS_NOMBRE)}&producto=${encodeURIComponent(nombre)}`);
    const d = await r.json();
    const demora = (d.demora || "").trim();
    if (demora) { badge.textContent = `⏱ ${demora} min`; badge.classList.remove("hidden"); }
  } catch { /* silencioso */ }
}

// El costo lo cotiza el CRM, no lo calculamos acá: es el número que después se
// descuenta de la campaña.
let costoActual = null;
let costoTimer = null;

function obtenerCosto() {
  clearTimeout(costoTimer);
  costoTimer = setTimeout(_obtenerCosto, 300);
}

async function _obtenerCosto() {
  const prod = calidadElegida();
  const cant = parseInt($("fw-cantidad").value || "0");
  costoActual = null;
  if (!prod || !cant || cant < 1) { $("fw-costo").value = "—"; return; }
  $("fw-costo").value = "…";
  try {
    const r = await fetch("/api/costo_trafico", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redsocial: RS_ID, producto: prod.id, cant_solicitada: cant }),
    });
    const d = await r.json();
    costoActual = d.costoTrafico != null ? parseFloat(d.costoTrafico) : null;
    $("fw-costo").value = costoActual != null ? plata(costoActual) : "—";
  } catch {
    $("fw-costo").value = "—";
  }
}

// ── Envío ──
function pedirFollowers() {
  hideError();
  const link = $("fw-link").value.trim();
  const usuario = igUsernameDe(link);
  const prod = calidadElegida();
  const cant = parseInt($("fw-cantidad").value || "0");

  if (!usuario) {
    $("fw-link-error").textContent = link
      ? "Ese link no es de un perfil (¿pegaste el de un post?)"
      : "Pegá el link del perfil";
    $("fw-link-error").classList.remove("hidden");
    $("fw-link").focus();
    return;
  }
  if (!prod) { $("fw-calidad-error").classList.remove("hidden"); return; }
  if (!cant || cant < 1) { $("fw-cantidad-error").classList.remove("hidden"); return; }

  const cliente = clienteElegido();
  const venta = $("fw-campana").value;
  if (!cliente && !venta) {
    hint("fw-campana-hint", "Elegí de qué campaña sale la plata.", true);
    $("fw-campana").focus();
    return;
  }

  // Resumen antes de gastar: la orden sale al panel y no se puede deshacer.
  $("fw-confirm-body").innerHTML = `
    <div class="fw-crow"><span>Perfil</span><b>@${esc(usuario)}</b></div>
    <div class="fw-crow"><span>Calidad</span><b>${esc(prod.nombre)}</b></div>
    <div class="fw-crow"><span>Cantidad</span><b>${cant.toLocaleString("es-AR")} followers</b></div>
    <div class="fw-crow"><span>Fondos</span><b>${cliente
      ? "Campaña de @" + esc(cliente.ig_username)
      : "Campaña #" + esc(venta)}</b></div>
    <div class="fw-crow fw-crow--total"><span>Costo</span><b>${costoActual != null ? plata(costoActual) : "a cotizar"}</b></div>`;
  $("fw-confirm").classList.remove("hidden");
}

function cerrarConfirm() { $("fw-confirm").classList.add("hidden"); }

async function enviarFollowers() {
  const usuario = igUsernameDe($("fw-link").value);
  const prod = calidadElegida();
  const cant = parseInt($("fw-cantidad").value || "0");
  const cliente = clienteElegido();
  const ok = $("fw-confirm-ok");
  ok.disabled = true;
  ok.textContent = "Enviando…";
  try {
    const orden = {
      redsocial_id: RS_ID,
      redsocial: RS_NOMBRE,
      prod: prod.nombre,
      demora: " - ",
      url: `https://www.instagram.com/${usuario}/`,
      costo: costoActual || 0,
      obs: "",
      cant_inicial: String(cant),
      cantidad: String(cant),
      programado: 0,
      fecha_programada: null,
      comentarios: [],
    };
    const r = await fetch("/api/enviar_trafico", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ordenes: [orden],
        costo_total: costoActual || 0,
        client: cliente ? cliente.ig_username : "",
        // Solo pesa cuando el perfil no es un cliente: el backend la valida.
        idventa: cliente ? "" : ($("fw-campana").value || ""),
        url: orden.url,
      }),
    });
    const d = await r.json().catch(() => ({}));
    cerrarConfirm();
    if (!r.ok || d.error || (d.errors && d.errors.length)) {
      throw new Error(d.error || (d.errors || []).join(" | ") || `Error ${r.status}`);
    }
    guardarPerfilReciente(usuario);
    pintarPerfilesRecientes();
    mostrarResultado(true, usuario, cant, prod.nombre, d);
    toast("✓ Orden enviada");
  } catch (e) {
    cerrarConfirm();
    mostrarResultado(false, usuario, cant, prod ? prod.nombre : "", null, e.message);
  } finally {
    ok.disabled = false;
    ok.textContent = "Enviar";
  }
}

function mostrarResultado(ok, usuario, cant, calidad, data, err) {
  const box = $("fw-resultado");
  const lineas = ok
    ? [`Órdenes insertadas: ${data.insertadas != null ? data.insertadas : 1}`,
       ...(data.messages || []), ...(data.warnings || [])].join("\n")
    : err;
  box.className = `fw-res ${ok ? "fw-res--ok" : "fw-res--bad"}`;
  box.innerHTML = `
    <div class="fw-res-t">${ok ? "✓" : "✕"} ${cant.toLocaleString("es-AR")} followers · ${esc(calidad)} → @${esc(usuario)}</div>
    <div class="fw-res-l">${esc(lineas)}</div>`;
  box.classList.remove("hidden");
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function confirmAbierto() {
  return !$("fw-confirm").classList.contains("hidden");
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") { cerrarConfirm(); return; }

  // Con el resumen abierto, Enter confirma: es el único paso que falta y ya se
  // leyó lo que se va a gastar.
  if (e.key === "Enter" && confirmAbierto()) {
    e.preventDefault();
    if (!$("fw-confirm-ok").disabled) enviarFollowers();
    return;
  }

  // "/" enfoca el campo del perfil, igual que en el home.
  if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !confirmAbierto()) {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || e.target.isContentEditable) return;
    e.preventDefault();
    $("fw-link").focus();
    $("fw-link").select();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  cargarClientes();
  cargarCalidades();
  pintarPerfilesRecientes();
  refrescarClearPerfil();
  validarPerfilVivo();
  $("fw-link").focus();

  // Enter en cualquier campo del formulario abre el resumen, no recarga.
  ["fw-link", "fw-cantidad"].forEach(id => {
    $(id).addEventListener("keydown", e => {
      if (e.key === "Enter" && !confirmAbierto()) { e.preventDefault(); pedirFollowers(); }
    });
  });
});
