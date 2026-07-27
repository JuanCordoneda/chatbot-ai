// Panel de admin self-serve (TAREA 3) — versión pulida. Clases con prefijo ax-.

// ── Tema ──
function toggleTheme() {
  const isLight = document.body.classList.toggle("light");
  localStorage.setItem("theme", isLight ? "light" : "dark");
  document.getElementById("btn-theme").textContent = isLight ? "🌙 Dark" : "☀ Light";
}
(function () {
  if (localStorage.getItem("theme") === "light") document.body.classList.add("light");
  document.addEventListener("DOMContentLoaded", () => {
    if (document.body.classList.contains("light"))
      document.getElementById("btn-theme").textContent = "🌙 Dark";
    const n = (document.getElementById("me-name").textContent || "A").trim();
    document.getElementById("me-av").textContent = (n[0] || "A").toUpperCase();
  });
})();

// ── Helpers ──
async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  let data = {}; try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
  return data;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function hue(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function avatarStyle(seed) {
  const h = hue(seed);
  return `background:linear-gradient(135deg,hsl(${h},62%,52%),hsl(${(h + 40) % 360},62%,44%))`;
}
function initials(name, handle) {
  const s = (name || handle || "?").trim();
  const parts = s.split(/\s+/);
  return ((parts[0][0] || "") + (parts[1] ? parts[1][0] : "")).toUpperCase() || "?";
}

let toastTimer = null;
function toast(msg, kind = "") {
  const t = document.getElementById("toast");
  const ico = kind === "bad"
    ? '<svg class="ax-ti" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="8" stroke="#ff6b6b" stroke-width="1.6"/><path d="M7 7l6 6M13 7l-6 6" stroke="#ff6b6b" stroke-width="1.6" stroke-linecap="round"/></svg>'
    : kind === "ok"
    ? '<svg class="ax-ti" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="8" stroke="#4ade80" stroke-width="1.6"/><path d="M6 10l3 3 5-6" stroke="#4ade80" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    : "";
  t.className = "ax-toast ax-on" + (kind ? " ax-" + kind : "");
  t.innerHTML = ico + esc(msg);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("ax-on"), 2400);
}

function switchTab(name) {
  document.querySelectorAll(".ax-seg .ax-tab").forEach(t => t.classList.toggle("ax-on", t.dataset.tab === name));
  document.querySelectorAll(".ax-panel").forEach(p => p.classList.remove("ax-on"));
  document.getElementById("panel-" + name).classList.add("ax-on");
  if (name === "uso") loadUso();
  if (name === "usuarios") loadUsuarios();
}

// ── Modales ──
function openMo(id) { document.getElementById(id).classList.add("ax-on"); }
function closeMo(id) { document.getElementById(id).classList.remove("ax-on"); }
document.addEventListener("click", e => {
  if (!e.target.classList.contains("ax-mo")) return;
  // El de cliente pasa por su propia guarda de cambios sin guardar.
  if (e.target.id === "client-mo") { closeClientModal(); return; }
  e.target.classList.remove("ax-on");
});
document.addEventListener("keydown", e => {
  const enClienteEditor = document.getElementById("client-mo").classList.contains("ax-on");
  const modal = document.querySelector("#client-mo .ax-modal");

  // ⌘/Ctrl + Enter guarda desde el textarea (Enter suelto sigue siendo salto de línea).
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && enClienteEditor) {
    e.preventDefault();
    document.getElementById("client-save").click();
    return;
  }
  // ⌥E entra y sale de pantalla completa sin soltar el teclado.
  if (enClienteEditor && e.altKey && (e.key === "e" || e.key === "E" || e.code === "KeyE")) {
    e.preventDefault();
    togglePromptFull();
    return;
  }
  if (e.key === "Escape") {
    // Con el confirm abierto, Esc = Cancelar (y resuelve la promesa que espera).
    if (document.getElementById("confirm-mo").classList.contains("ax-on")) {
      document.getElementById("confirm-cancel").click();
      return;
    }
    // Estando en pantalla completa, el primer Esc solo vuelve al modal normal.
    if (enClienteEditor && modal.classList.contains("ax-modal--full")) { togglePromptFull(); return; }
    if (enClienteEditor) { closeClientModal(); return; }
    document.querySelectorAll(".ax-mo.ax-on").forEach(m => m.classList.remove("ax-on"));
  }
  if (e.key === "Enter" && !e.shiftKey) {
    const open = document.querySelector(".ax-mo.ax-on");
    if (!open) return;
    if (document.activeElement && document.activeElement.tagName === "TEXTAREA") return;
    const save = open.querySelector(".ax-btn--primary, #confirm-ok");
    if (save) { e.preventDefault(); save.click(); }
  }
});

// Cualquier campo del modal de cliente (no solo el prompt) refresca el aviso de
// "cambios sin guardar".
["input", "change"].forEach(ev =>
  document.addEventListener(ev, e => {
    if (e.target.closest && e.target.closest("#client-mo")) updatePromptCount();
  })
);

function confirmDialog({ title, text, okLabel = "Borrar" }) {
  return new Promise(resolve => {
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-text").textContent = text;
    const ok = document.getElementById("confirm-ok");
    ok.textContent = okLabel;
    const cancel = document.getElementById("confirm-cancel");
    // "Cancelar" TIENE que resolver la promesa: antes solo cerraba el modal y
    // quien esperaba el confirm quedaba colgado para siempre.
    const done = v => { closeMo("confirm-mo"); ok.onclick = null; cancel.onclick = null; resolve(v); };
    ok.onclick = () => done(true);
    cancel.onclick = () => done(false);
    document.getElementById("confirm-mo").onclick = e => { if (e.target.id === "confirm-mo") done(false); };
    openMo("confirm-mo");
  });
}

function showErr(id, msg) { const e = document.getElementById(id); e.textContent = msg; e.classList.add("ax-on"); }
function hideErr(id) { document.getElementById(id).classList.remove("ax-on"); }

// Vendedor (cuenta) seleccionado para la pestaña Clientes.
let selectedVendedor = null;

// Modo de la página: admin (gestiona todas las cuentas) o vendedor (solo la suya).
const IS_ADMIN = document.body.dataset.isAdmin === "true";
const MY_ACCOUNT = parseInt(document.body.dataset.account || "0") || null;
function setTxt(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }

// ── KPIs ──
function renderKpis() {
  const act = clientsCache.filter(c => c.status === "active").length;
  const pau = clientsCache.filter(c => c.status === "paused").length;
  const venAct = vendedoresCache.filter(v => v.active).length;
  setTxt("kpi-cli-act", selectedVendedor ? act : "–");
  setTxt("kpi-cli-sub", selectedVendedor ? `${clientsCache.length} en total` : "elegí un vendedor");
  setTxt("kpi-cli-pau", selectedVendedor ? pau : "–");
  setTxt("kpi-ven-act", venAct);
  const inact = vendedoresCache.length - venAct;
  setTxt("kpi-ven-sub", inact ? `${inact} inactivo${inact === 1 ? "" : "s"}` : "todos activos");
  setTxt("tab-cli-cnt", selectedVendedor ? clientsCache.length : 0);
  setTxt("tab-ven-cnt", vendedoresCache.length);
  setTxt("tab-usr-cnt", selectedVendedor ? usuariosCache.length : 0);
}

// ── Clientes ──
let clientsCache = [];

// Los endpoints de clientes están scoped al vendedor elegido (?vendedor=<id>).
function cliUrl(path = "") {
  return `/api/admin/clients${path}?vendedor=${selectedVendedor}`;
}

async function loadClients() {
  const list = document.getElementById("clientes-list");
  if (!selectedVendedor) {
    clientsCache = [];
    list.innerHTML = emptyState("Elegí un vendedor",
      vendedoresCache.length ? "Seleccioná un vendedor arriba para ver y editar sus clientes."
                             : "Creá primero un vendedor en la pestaña Vendedores.");
    renderKpis();
    return;
  }
  list.innerHTML =
    '<div class="ax-skeleton"></div><div class="ax-skeleton"></div><div class="ax-skeleton"></div>';
  try {
    const { clients } = await api("GET", cliUrl());
    clientsCache = clients;
    renderClients(); renderKpis();
  } catch (e) { toast(e.message, "bad"); }
}

// Selector de vendedor: repuebla el <select> y aplica el elegido (localStorage).
function renderVendedorSelect() {
  const sel = document.getElementById("vendedor-select");
  if (!vendedoresCache.length) {
    sel.innerHTML = '<option value="">— sin vendedores —</option>';
    selectedVendedor = null;
    return;
  }
  const saved = selectedVendedor || parseInt(localStorage.getItem("admin_vendedor") || "0");
  const valid = vendedoresCache.some(v => v.id === saved);
  selectedVendedor = valid ? saved : vendedoresCache[0].id;
  localStorage.setItem("admin_vendedor", String(selectedVendedor));
  sel.innerHTML = vendedoresCache.map(v =>
    `<option value="${v.id}" ${v.id === selectedVendedor ? "selected" : ""}>${esc(v.name)}${v.active ? "" : " (inactivo)"}</option>`
  ).join("");
}

function onVendedorChange() {
  const v = parseInt(document.getElementById("vendedor-select").value || "0");
  selectedVendedor = v || null;
  if (selectedVendedor) localStorage.setItem("admin_vendedor", String(selectedVendedor));
  loadClients();
  loadUsuarios();
}

// íconos de género para los encabezados de grupo
const GENDER_ICON = {
  male: '<svg viewBox="0 0 16 16" fill="none"><circle cx="6.5" cy="9.5" r="4" stroke="currentColor" stroke-width="1.4"/><path d="M10 6l4-4m0 0h-3.5M14 2v3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  female: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="6" r="4" stroke="currentColor" stroke-width="1.4"/><path d="M8 10v5M6 13h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  none: '<svg viewBox="0 0 16 16" fill="none"><circle cx="6" cy="6" r="2.4" stroke="currentColor" stroke-width="1.3"/><path d="M2 13c0-2.2 1.8-3.5 4-3.5s4 1.3 4 3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M11 4.2a2.4 2.4 0 010 4.6M13 13c0-2-1-3.2-2.5-3.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
};

function clientCard(c) {
  return `
    <div class="ax-card ${c.status === "paused" ? "ax-dimmed" : ""}">
      <div class="ax-avatar" style="${avatarStyle(c.ig_username)}">${esc(initials(c.display_name, c.ig_username))}</div>
      <div class="ax-main">
        <div class="ax-name">${esc(c.display_name) || '<span style="color:var(--faint)">(sin nombre)</span>'}
          ${c.status === "active"
            ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Activo</span>'
            : '<span class="ax-pill ax-pill--paused"><span class="ax-pdot"></span>Pausado</span>'}
        </div>
        <div class="ax-sub"><span class="ax-handle" title="Copiar" onclick="copyHandle('${esc(c.ig_username)}')">@${esc(c.ig_username)}</span>
          · ${(c.prompt || "").length} car. de prompt</div>
      </div>
      <div class="ax-acts">
        <button class="ax-btn ax-btn--sm" onclick="openClientModal(${c.id})">Editar</button>
        <button class="ax-btn ax-btn--sm" onclick="togglePause(${c.id})">${c.status === "active" ? "Pausar" : "Activar"}</button>
        <button class="ax-btn ax-btn--sm ax-btn--danger ax-btn--icon" title="Borrar" onclick="deleteClient(${c.id})">
          <svg viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3h4v1M5 4l.5 9h5L11 4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
    </div>`;
}

function renderClients() {
  const q = (document.getElementById("cli-search").value || "").trim().toLowerCase();
  const list = document.getElementById("clientes-list");
  const items = clientsCache.filter(c =>
    !q || c.ig_username.includes(q) || (c.display_name || "").toLowerCase().includes(q));
  if (!clientsCache.length) { list.innerHTML = emptyState("Todavía no hay clientes", "Creá el primero con su @usuario y su prompt."); return; }
  if (!items.length) { list.innerHTML = emptyState("Sin resultados", "Probá con otro nombre o @usuario."); return; }

  // Agrupación por género del cliente: Hombres, Mujeres, Sin especificar.
  const GROUPS = [
    { key: "male", label: "Hombres" },
    { key: "female", label: "Mujeres" },
    { key: "none", label: "Mixto" },
  ];
  const inGroup = (c, key) => key === "none" ? (c.gender !== "male" && c.gender !== "female") : c.gender === key;

  let html = "";
  for (const g of GROUPS) {
    const gi = items.filter(c => inGroup(c, g.key));
    if (!gi.length) continue;
    html += `<div class="ax-group">${GENDER_ICON[g.key]}<span class="ax-group-t">${g.label}</span><span class="ax-group-c">${gi.length}</span><span class="ax-group-line"></span></div>`;
    html += gi.map(clientCard).join("");
  }
  list.innerHTML = html;
}

function emptyState(t, s) {
  return `<div class="ax-empty">
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none"><rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M3 9h18" stroke="currentColor" stroke-width="1.5"/></svg>
    <div class="ax-et">${esc(t)}</div><div>${esc(s)}</div></div>`;
}

function copyHandle(h) {
  navigator.clipboard?.writeText("@" + h).then(() => toast("@" + h + " copiado", "ok")).catch(() => {});
}

// Snapshot del formulario al abrir: sirve para avisar si se cierra con cambios
// sin guardar (perder un prompt largo por un Esc de más es lo peor que puede
// pasar en esta pantalla).
let clientSnapshot = "";

function _clientFormState() {
  const v = (id) => document.getElementById(id).value;
  return JSON.stringify([
    v("client-ig"), v("client-name"), v("client-status"), v("client-gender"), v("client-prompt"),
    ...["likes", "views", "shares"].flatMap(k => [v(`range-${k}-min`), v(`range-${k}-max`)]),
  ]);
}

function clientIsDirty() { return _clientFormState() !== clientSnapshot; }

function updatePromptCount() {
  const txt = document.getElementById("client-prompt").value;
  const palabras = txt.trim() ? txt.trim().split(/\s+/).length : 0;
  document.getElementById("client-prompt-count").textContent =
    `${txt.length} caracteres · ${palabras} palabra${palabras === 1 ? "" : "s"}`;
  document.getElementById("client-dirty").classList.toggle("ax-on", clientIsDirty());
}

// Pantalla completa del editor: esconde la columna de datos y deja el textarea
// a toda la ventana, para prompts largos.
function togglePromptFull() {
  const modal = document.querySelector("#client-mo .ax-modal");
  const full = modal.classList.toggle("ax-modal--full");
  modal.querySelector(".ax-editor-btn-ico").textContent = full ? "⤡" : "⤢";
  document.getElementById("client-prompt-expand-txt").textContent = full ? "Achicar prompt" : "Agrandar prompt";
  document.getElementById("client-prompt").focus();
}

// Cierre con guarda: si hay cambios, se pregunta antes de descartar.
async function closeClientModal() {
  if (clientIsDirty()) {
    const ok = await confirmDialog({
      title: "¿Descartar los cambios?",
      text: "Editaste el cliente y todavía no guardaste. Si cerrás, se pierde lo que escribiste.",
      okLabel: "Descartar",
    });
    if (!ok) return;
  }
  document.querySelector("#client-mo .ax-modal").classList.remove("ax-modal--full");
  closeMo("client-mo");
}

function openClientModal(id) {
  if (!selectedVendedor) { toast("Elegí un vendedor primero", "bad"); return; }
  hideErr("client-err");
  const c = id ? clientsCache.find(x => x.id === id) : null;
  document.getElementById("client-title").textContent = c ? "Editar cliente" : "Nuevo cliente";
  // Editando no hace falta bajada: el título ya dice todo.
  const sub = document.getElementById("client-subtitle");
  sub.textContent = c ? "" : "Se guarda en la base y el motor lo usa al instante, sin deploy.";
  sub.style.display = c ? "none" : "";
  document.getElementById("client-id").value = c ? c.id : "";
  document.getElementById("client-ig").value = c ? c.ig_username : "";
  document.getElementById("client-name").value = c ? c.display_name : "";
  document.getElementById("client-status").value = c ? c.status : "active";
  document.getElementById("client-gender").value = c && c.gender ? c.gender : "";
  const rg = (c && c.ranges) || {};
  for (const k of ["likes", "views", "shares"]) {
    document.getElementById(`range-${k}-min`).value = rg[k] && rg[k].min != null ? rg[k].min : "";
    document.getElementById(`range-${k}-max`).value = rg[k] && rg[k].max != null ? rg[k].max : "";
  }
  document.getElementById("client-prompt").value = c ? c.prompt : "";
  document.querySelector("#client-mo .ax-modal").classList.remove("ax-modal--full");
  document.querySelector("#client-mo .ax-editor-btn-ico").textContent = "⤢";
  document.getElementById("client-prompt-expand-txt").textContent = "Agrandar prompt";
  clientSnapshot = _clientFormState();
  updatePromptCount();
  openMo("client-mo");
  // Editando un cliente que ya existe, lo que se viene a tocar es el prompt.
  setTimeout(() => document.getElementById(c ? "client-prompt" : "client-ig").focus(), 50);
}

async function saveClient() {
  const id = document.getElementById("client-id").value;
  const ranges = {};
  for (const k of ["likes", "views", "shares"]) {
    const mn = document.getElementById(`range-${k}-min`).value;
    const mx = document.getElementById(`range-${k}-max`).value;
    if (mn !== "" && mx !== "") ranges[k] = { min: parseInt(mn), max: parseInt(mx) };
  }
  const payload = {
    ig_username: document.getElementById("client-ig").value,
    display_name: document.getElementById("client-name").value,
    status: document.getElementById("client-status").value,
    gender: document.getElementById("client-gender").value,
    ranges,
    prompt: document.getElementById("client-prompt").value,
  };
  const btn = document.getElementById("client-save"); btn.disabled = true;
  try {
    if (id) await api("PATCH", cliUrl(`/${id}`), payload);
    else await api("POST", cliUrl(), payload);
    clientSnapshot = _clientFormState();   // guardado ⇒ ya no hay cambios pendientes
    document.querySelector("#client-mo .ax-modal").classList.remove("ax-modal--full");
    closeMo("client-mo");
    toast(id ? "Cliente actualizado" : "Cliente creado", "ok");
    loadClients();
  } catch (e) { showErr("client-err", e.message); } finally { btn.disabled = false; }
}

async function togglePause(id) {
  const c = clientsCache.find(x => x.id === id); if (!c) return;
  try {
    await api("PATCH", cliUrl(`/${id}`), { status: c.status === "active" ? "paused" : "active" });
    toast(c.status === "active" ? "Cliente pausado" : "Cliente activado", "ok");
    loadClients();
  } catch (e) { toast(e.message, "bad"); }
}

async function deleteClient(id) {
  const c = clientsCache.find(x => x.id === id);
  const ok = await confirmDialog({
    title: "Borrar cliente",
    text: `Se eliminará @${c ? c.ig_username : id} y su prompt. Esta acción no se puede deshacer.`,
  });
  if (!ok) return;
  try { await api("DELETE", cliUrl(`/${id}`)); toast("Cliente borrado", "ok"); loadClients(); }
  catch (e) { toast(e.message, "bad"); }
}

// ── Vendedores (cuentas con credenciales de Growi) ──
let vendedoresCache = [];

async function loadVendedores() {
  try {
    const { vendedores } = await api("GET", "/api/admin/vendedores");
    vendedoresCache = vendedores;
    renderVendedores();
    renderVendedorSelect();
    renderKpis();
  } catch (e) { toast(e.message, "bad"); }
}

function renderVendedores() {
  const q = (document.getElementById("ven-search").value || "").trim().toLowerCase();
  const list = document.getElementById("vendedores-list");
  const items = vendedoresCache.filter(v =>
    !q || v.name.toLowerCase().includes(q) || (v.crm_email || "").toLowerCase().includes(q));
  if (!vendedoresCache.length) { list.innerHTML = emptyState("Sin vendedores", "Creá el primero con su email y contraseña de Growi."); return; }
  if (!items.length) { list.innerHTML = emptyState("Sin resultados", "Probá con otro nombre o email."); return; }
  list.innerHTML = items.map(v => `
    <div class="ax-card ${v.active ? "" : "ax-dimmed"}">
      <div class="ax-avatar" style="${avatarStyle(v.name)}">${esc((v.name[0] || "?").toUpperCase())}</div>
      <div class="ax-main">
        <div class="ax-name">${esc(v.name)}
          ${v.active ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Activo</span>' : '<span class="ax-pill ax-pill--off"><span class="ax-pdot"></span>Inactivo</span>'}
          ${v.has_password ? "" : '<span class="ax-pill ax-pill--paused" title="Sin contraseña guardada">sin pass</span>'}
        </div>
        <div class="ax-sub">${esc(v.crm_email || "(sin email)")}${v.crm_idvendedor ? ` · id ${esc(v.crm_idvendedor)}` : ""}</div>
      </div>
      <div class="ax-acts">
        <button class="ax-btn ax-btn--sm" onclick="openVendorModal(${v.id})">Editar</button>
        <button class="ax-btn ax-btn--sm" onclick="toggleVendorActive(${v.id})">${v.active ? "Desactivar" : "Activar"}</button>
      </div>
    </div>`).join("");
}

function openVendorModal(id) {
  hideErr("ven-err");
  const v = id ? vendedoresCache.find(x => x.id === id) : null;
  document.getElementById("ven-title").textContent = v ? "Editar vendedor" : "Nuevo vendedor";
  document.getElementById("ven-subtitle").textContent = v
    ? "Actualizá sus datos o credenciales de Growi."
    : "El vendedor entra a la plataforma con este email y contraseña de Growi.";
  document.getElementById("ven-pass-hint").textContent = v ? "dejala vacía para no cambiarla" : "la que usa en el CRM";
  document.getElementById("ven-id").value = v ? v.id : "";
  document.getElementById("ven-name").value = v ? v.name : "";
  document.getElementById("ven-email").value = v ? (v.crm_email || "") : "";
  document.getElementById("ven-pass").value = "";
  document.getElementById("ven-idvendedor").value = v ? (v.crm_idvendedor || "") : "";
  document.getElementById("ven-idventa").value = v ? (v.crm_idventa || "") : "";
  document.getElementById("ven-disponible").value = v ? (v.crm_disponible || "") : "";
  document.getElementById("ven-url").value = v ? (v.crm_url || "") : "https://crm.growiagency.com";
  document.getElementById("ven-proxy").value = v ? (v.crm_proxy || "") : "";
  openMo("ven-mo");
  setTimeout(() => document.getElementById("ven-name").focus(), 50);
}

async function saveVendor() {
  const id = document.getElementById("ven-id").value;
  const payload = {
    name: document.getElementById("ven-name").value,
    crm_email: document.getElementById("ven-email").value,
    crm_idvendedor: document.getElementById("ven-idvendedor").value,
    crm_idventa: document.getElementById("ven-idventa").value,
    crm_disponible: document.getElementById("ven-disponible").value,
    crm_url: document.getElementById("ven-url").value,
    crm_proxy: document.getElementById("ven-proxy").value,
  };
  const pass = document.getElementById("ven-pass").value;
  // En alta la contraseña es obligatoria; en edición, vacía = no tocar.
  if (pass || !id) payload.crm_password = pass;
  const btn = document.getElementById("ven-save"); btn.disabled = true;
  try {
    if (id) await api("PATCH", `/api/admin/vendedores/${id}`, payload);
    else await api("POST", "/api/admin/vendedores", payload);
    closeMo("ven-mo");
    toast(id ? "Vendedor actualizado" : "Vendedor creado", "ok");
    await loadVendedores();
    if (!id) loadClients();
  } catch (e) { showErr("ven-err", e.message); } finally { btn.disabled = false; }
}

async function toggleVendorActive(id) {
  const v = vendedoresCache.find(x => x.id === id); if (!v) return;
  if (v.active) {
    const ok = await confirmDialog({
      title: "Desactivar vendedor",
      text: `${v.name} no va a poder iniciar sesión hasta que lo reactives.`,
      okLabel: "Desactivar",
    });
    if (!ok) return;
  }
  try {
    await api("PATCH", `/api/admin/vendedores/${id}`, { active: !v.active });
    toast(v.active ? "Vendedor desactivado" : "Vendedor activado", "ok");
    await loadVendedores();
    loadClients();
  } catch (e) { toast(e.message, "bad"); }
}

// ── Uso ──
async function loadUso() {
  const box = document.getElementById("uso-list");
  try {
    const { vendedores } = await api("GET", "/api/uso");
    const vs = vendedores || [];
    document.getElementById("kpi-acc").textContent = vs.reduce((s, v) => s + v.total, 0);
    if (!vs.length) { box.innerHTML = emptyState("Sin actividad", "Cuando el equipo genere o publique, lo verás acá."); return; }
    const max = Math.max(1, ...vs.map(v => v.total));
    box.innerHTML = vs.map(v => {
      const seg = (n, cls, lbl) => n ? `<div class="ax-uso-seg ${cls}" style="flex:${n}" title="${lbl}: ${n}">${n}</div>` : "";
      const barW = v.total ? (v.total / max) * 100 : 3;
      const nm = v.name || v.crm_email || "?";
      return `<div class="ax-uso-row">
        <div class="ax-uso-name">
          <div class="ax-avatar" style="width:28px;height:28px;border-radius:8px;font-size:.78rem;${avatarStyle(nm)}">${esc((nm[0] || "?").toUpperCase())}</div>
          ${esc(nm)}
        </div>
        <div class="ax-uso-bar" style="width:${barW}%">
          ${seg(v.generar, "ax-g", "Generar")}${seg(v.publicar, "ax-p", "Publicar")}${seg(v.enviar_trafico, "ax-e", "Enviar tráfico")}
        </div>
        <div class="ax-uso-total">${v.total}</div>
      </div>`;
    }).join("");
  } catch (e) { toast(e.message, "bad"); }
}

// ── Usuarios (logins) ────────────────────────────────────────────────────────
// Cada usuario es un login propio dentro de la cuenta del vendedor elegido = un
// asiento de la suscripción. Antes había que tocar código y redeployar.
let usuariosCache = [];

function usrUrl(path = "") {
  return `/api/admin/usuarios${path}?vendedor=${selectedVendedor}`;
}

async function loadUsuarios() {
  const list = document.getElementById("usuarios-list");
  if (!list) return;
  if (!selectedVendedor) {
    usuariosCache = [];
    list.innerHTML = emptyState("Elegí un vendedor",
      vendedoresCache.length ? "Seleccioná un vendedor arriba para ver sus usuarios."
                             : "Creá primero un vendedor en la pestaña Vendedores.");
    renderKpis();
    return;
  }
  list.innerHTML = '<div class="ax-skeleton"></div><div class="ax-skeleton"></div>';
  try {
    const { usuarios } = await api("GET", usrUrl());
    usuariosCache = usuarios;
    renderUsuarios(); renderKpis();
  } catch (e) { toast(e.message, "bad"); }
}

function renderUsuarios() {
  const list = document.getElementById("usuarios-list");
  if (!list) return;
  const q = (document.getElementById("usr-search").value || "").trim().toLowerCase();
  const items = usuariosCache.filter(u => !q || u.username.toLowerCase().includes(q));
  if (!usuariosCache.length) {
    list.innerHTML = emptyState("Sin usuarios", "Creá el primer login para este vendedor.");
    return;
  }
  if (!items.length) { list.innerHTML = emptyState("Sin resultados", "Probá con otro nombre."); return; }
  list.innerHTML = items.map(u => `
    <div class="ax-card ${u.active ? "" : "ax-dimmed"}">
      <div class="ax-avatar" style="${avatarStyle(u.username)}">${esc((u.username[0] || "?").toUpperCase())}</div>
      <div class="ax-main">
        <div class="ax-name">${esc(u.username)}
          ${u.active ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Activo</span>'
                     : '<span class="ax-pill ax-pill--off"><span class="ax-pdot"></span>Inactivo</span>'}
          ${u.role === "admin" ? '<span class="ax-pill ax-pill--paused">admin</span>' : ""}
        </div>
        <div class="ax-sub">${u.role === "admin" ? "Puede entrar al panel de administración" : "Acceso a la herramienta de comentarios"}</div>
      </div>
      <div class="ax-acts">
        <button class="ax-btn ax-btn--sm" onclick="openUserModal(${u.id})">Editar</button>
        <button class="ax-btn ax-btn--sm" onclick="toggleUserActive(${u.id})">${u.active ? "Desactivar" : "Activar"}</button>
      </div>
    </div>`).join("");
}

function openUserModal(id) {
  hideErr("usr-err");
  if (!selectedVendedor) { toast("Elegí un vendedor primero", "bad"); return; }
  const u = id ? usuariosCache.find(x => x.id === id) : null;
  document.getElementById("usr-title").textContent = u ? "Editar usuario" : "Nuevo usuario";
  document.getElementById("usr-id").value = u ? u.id : "";
  document.getElementById("usr-username").value = u ? u.username : "";
  document.getElementById("usr-username").disabled = !!u;   // el usuario no se renombra
  document.getElementById("usr-pass").value = "";
  document.getElementById("usr-pass-hint").textContent = u ? "vacía = no cambiarla" : "mínimo 4 caracteres";
  document.getElementById("usr-role").value = u ? u.role : "vendedor";
  openMo("usr-mo");
}

async function saveUser() {
  const id = document.getElementById("usr-id").value;
  const pass = document.getElementById("usr-pass").value;
  const payload = {
    account_id: selectedVendedor,
    role: document.getElementById("usr-role").value,
  };
  if (!id) {
    payload.username = document.getElementById("usr-username").value;
    payload.password = pass;
  } else if (pass) {
    payload.password = pass;   // en edición, vacía = no tocar
  }
  try {
    if (id) await api("PATCH", `/api/admin/usuarios/${id}?vendedor=${selectedVendedor}`, payload);
    else await api("POST", usrUrl(), payload);
    closeMo("usr-mo");
    toast(id ? "Usuario actualizado" : "Usuario creado", "ok");
    await loadUsuarios();
  } catch (e) { showErr("usr-err", e.message); }
}

async function toggleUserActive(id) {
  const u = usuariosCache.find(x => x.id === id); if (!u) return;
  if (u.active) {
    const ok = await confirmDialog({
      title: "Desactivar usuario",
      text: `${u.username} no va a poder iniciar sesión hasta que lo reactives.`,
      okLabel: "Desactivar",
    });
    if (!ok) return;
  }
  try {
    await api("PATCH", `/api/admin/usuarios/${id}?vendedor=${selectedVendedor}`, { account_id: selectedVendedor, active: !u.active });
    toast(u.active ? "Usuario desactivado" : "Usuario activado", "ok");
    await loadUsuarios();
  } catch (e) { toast(e.message, "bad"); }
}

// ── init ──
// Primero los vendedores: definen el selector y el vendedor por defecto; recién
// entonces cargamos los clientes de ese vendedor.
(async function init() {
  if (IS_ADMIN) {
    await loadVendedores();
    loadClients();
    loadUsuarios();
    loadUso();
  } else {
    // Modo vendedor: solo sus clientes. selectedVendedor truthy para pasar los
    // guards; el backend usa la cuenta de la sesión (ignora el ?vendedor).
    selectedVendedor = MY_ACCOUNT || "me";
    loadClients();
  }
})();
