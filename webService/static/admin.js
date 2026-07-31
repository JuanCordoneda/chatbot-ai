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
  if (name === "pedidos") loadPedidos();
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
    if (!e.target.closest || !e.target.closest("#client-mo")) return;
    // Al cambiar el @usuario cambian las campañas del cliente: se repuebla la lista.
    if (e.target.id === "client-ig") {
      renderVentaSelect(e.target.value);
      actualizarVentaHint();
    }
    updatePromptCount();
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
  const pend = vendedoresCache.filter(v => v.status === "pending").length;
  const inact = vendedoresCache.length - venAct - pend;
  setTxt("kpi-ven-sub", pend
    ? `${pend} esperando habilitación`
    : (inact ? `${inact} sin acceso` : "todos activos"));
  setTxt("tab-cli-cnt", selectedVendedor ? clientsCache.length : 0);
  setTxt("tab-ven-cnt", vendedoresCache.length);
  setTxt("tab-usr-cnt", selectedVendedor ? usuariosCache.length : 0);
}

// ── Clientes ──
let clientsCache = [];
// Cliente genérico del sistema (solo lo recibe el admin). Ver genericCard().
let genericCache = null;

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
    // Las ventas del CRM se piden en paralelo: la tarjeta de cada cliente muestra
    // el nombre y el saldo de la suya, no solo el id.
    const [{ clients }] = await Promise.all([api("GET", cliUrl()), loadVentas()]);
    // El genérico viaja en la misma respuesta (solo para el admin) pero se
    // guarda aparte: no es un cliente del vendedor y no cuenta en los KPIs.
    genericCache = clients.find(c => c.reserved) || null;
    clientsCache = clients.filter(c => !c.reserved);
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
  // Los que están esperando habilitación no operan todavía: no se pueden elegir.
  const elegibles = vendedoresCache.filter(v => v.status !== "pending");
  if (!elegibles.length) {
    sel.innerHTML = '<option value="">— sin vendedores habilitados —</option>';
    selectedVendedor = null;
    return;
  }
  const saved = selectedVendedor || parseInt(localStorage.getItem("admin_vendedor") || "0");
  const valid = elegibles.some(v => v.id === saved);
  selectedVendedor = valid ? saved : elegibles[0].id;
  localStorage.setItem("admin_vendedor", String(selectedVendedor));
  sel.innerHTML = elegibles.map(v =>
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

// ── Ventas del CRM: de dónde salen los fondos de cada cliente ────────────────
// Hasta ahora TODO el tráfico se descontaba del idventa del .env (una sola venta
// para todos los clientes). Cada cliente apunta ahora a la suya.
let ventasCache = [];
let ventasError = "";

async function loadVentas() {
  try {
    const r = await api("GET", "/api/ventas");
    ventasCache = r.ventas || [];
    ventasError = "";
  } catch (e) {
    ventasCache = [];
    ventasError = e.message || "No se pudieron leer las ventas del CRM";
  }
  renderVentaSelect();
}

// Saldo por debajo del cual la campaña ya no alcanza para mandar tráfico: es el
// aviso que faltaba (la del .env venía descontando hasta quedar en $7).
const VENTA_SALDO_BAJO = 5;

function ventaById(id) {
  return ventasCache.find(v => String(v.idventa) === String(id)) || null;
}

function saldoDe(v) { return parseFloat((v && v.disponible) || 0) || 0; }

// Campañas de un cliente: se agrupan por el PERFIL de IG que trae el CRM, no por
// el nombre — el mismo cliente figura como "Peter J Fouernier", "Peter Fournier"
// y "Peter Fouernier" según quién la cargó.
function ventasDe(igUsername) {
  const ig = (igUsername || "").trim().toLowerCase();
  if (!ig) return [];
  return ventasCache.filter(v => v.ig_username === ig);
}

function fmtSaldo(n) { return `$${n.toFixed(2)}`; }

// Lo que elige el backend cuando el cliente no tiene campaña asignada a mano:
// la ÚLTIMA campaña de su perfil (activa si tiene alguna). Se replica acá para
// que la pantalla muestre exactamente lo que se va a cobrar.
function ultimaCampana(igUsername) {
  const propias = ventasDe(igUsername);
  if (!propias.length) return null;
  const activas = propias.filter(v => v.activa);
  const pool = activas.length ? activas : propias;
  return pool.reduce((a, b) => ((b.fecha || "") > (a.fecha || "") ? b : a));
}

// La campaña que conviene proponer: activa, con más saldo, del propio cliente.
function ventaSugerida(igUsername) {
  const propias = ventasDe(igUsername).filter(v => v.activa && saldoDe(v) >= VENTA_SALDO_BAJO);
  return propias.length ? propias.reduce((a, b) => saldoDe(b) > saldoDe(a) ? b : a) : null;
}

// Estado de fondos de un cliente, en un solo lugar: lo usan la tarjeta, el
// modal y el contador de "faltan asignar".
function estadoFondos(c) {
  const propias = ventasDe(c.ig_username);
  const total = propias.reduce((a, v) => a + saldoDe(v), 0);
  const id = (c.crm_idventa || "").trim();
  let v = id ? ventaById(id) : null;
  let auto = false;
  if (!v) {
    // Sin asignación manual (o con una que ya no existe) se usa la última
    // campaña del propio cliente. La por defecto es el último recurso.
    v = ultimaCampana(c.ig_username);
    auto = !!v;
  }
  const base = { propias, total, venta: v, auto };
  if (!v) return { ...base, nivel: "warn", texto: "Sin campaña propia",
    detalle: "Este perfil no tiene campañas en el CRM: paga la campaña por defecto de la cuenta." };
  const saldo = saldoDe(v);
  const suf = auto ? " · auto" : "";
  if (saldo < VENTA_SALDO_BAJO) return { ...base, nivel: "bad", texto: `Queda ${fmtSaldo(saldo)}`,
    detalle: "Casi sin saldo: pasalo a otra campaña de este cliente." };
  if (!v.activa) return { ...base, nivel: "warn", texto: `${fmtSaldo(saldo)} · campaña vieja`,
    detalle: "Ya no figura entre las campañas activas del CRM." };
  return { ...base, nivel: "ok", texto: fmtSaldo(saldo) + suf, detalle: "" };
}

// Chip de fondos en la tarjeta del cliente. Dice de dónde sale la plata en
// castellano, no un id suelto.
function ventaBadge(c) {
  const f = estadoFondos(c);
  const icono = f.nivel === "ok" ? "💰" : "⚠";
  const resumen = f.propias.length > 1
    ? `<span class="ax-venta-extra" title="Este cliente tiene ${f.propias.length} campañas en el CRM, sumando ${fmtSaldo(f.total)}">+${f.propias.length - 1} camp. · ${fmtSaldo(f.total)} en total</span>`
    : "";
  const cls = f.nivel === "ok" ? "" : ` ax-venta--${f.nivel}`;
  const title = `Paga con: ${f.venta ? `#${f.venta.idventa} ${f.venta.nombre}` : "—"}${f.detalle ? ". " + f.detalle : ""}`;
  return `<span class="ax-venta${cls}" title="${esc(title)}">${icono} ${esc(f.texto)}</span>${resumen}`;
}

function _ventaOption(v) {
  const marca = v.activa ? "" : " · vieja";
  return `<option value="${esc(v.idventa)}">#${esc(v.idventa)} · ${fmtSaldo(saldoDe(v))} · ${esc(v.nombre)}${marca}</option>`;
}

// Repuebla el <select> del modal. Primero las campañas DEL cliente (que es entre
// las que se va a querer ir moviendo), después el resto.
function renderVentaSelect(igUsername) {
  const sel = document.getElementById("client-venta");
  if (!sel) return;
  const actual = sel.value;
  const ig = (igUsername || "").trim().toLowerCase();
  const propias = ventasDe(ig);
  const otras = ventasCache.filter(v => v.ig_username !== ig);

  const ult = ultimaCampana(ig);
  let html = `<option value="">Automático — ${ult ? `última campaña (#${esc(ult.idventa)} · ${fmtSaldo(saldoDe(ult))})` : "campaña por defecto de la cuenta"}</option>`;
  if (propias.length) {
    const total = propias.reduce((a, v) => a + saldoDe(v), 0);
    html += `<optgroup label="Campañas de @${esc(ig)} — ${fmtSaldo(total)} disponibles">${propias.map(_ventaOption).join("")}</optgroup>`;
  }
  if (otras.length) {
    html += `<optgroup label="${propias.length ? "Otras campañas" : "Todas las campañas"}">${otras.map(_ventaOption).join("")}</optgroup>`;
  }
  sel.innerHTML = html;
  sel.value = actual;
  actualizarVentaHint();
}

// Ficha de la campaña elegida, debajo del select: saldo grande, a nombre de
// quién está y desde cuándo. Es lo que responde "¿de dónde sale la plata?".
function actualizarVentaHint() {
  const hint = document.getElementById("client-venta-hint");
  const box = document.getElementById("client-venta-detalle");
  if (!hint || !box) return;

  const ig = (document.getElementById("client-ig").value || "").trim().toLowerCase();
  const propias = ventasDe(ig);
  hint.textContent = ventasError
    ? ventasError
    : (propias.length
        ? `${propias.length} campaña${propias.length === 1 ? "" : "s"} de @${ig} · ${fmtSaldo(propias.reduce((a, v) => a + saldoDe(v), 0))} en total`
        : `${ventasCache.length} campañas en el CRM`);

  const v = ventaById(document.getElementById("client-venta").value);
  if (!v) {
    // Sin elección manual manda el automático: la última campaña del cliente.
    const ult = ultimaCampana(ig);
    if (ult) {
      box.className = "ax-venta-detalle";
      box.innerHTML = `
        <div class="ax-vd-main">
          <div class="ax-vd-title">Automático · ${fmtSaldo(saldoDe(ult))}
            <span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Última campaña</span>
          </div>
          <div class="ax-vd-sub">Va a cobrar de la #${esc(ult.idventa)} · ${esc(ult.nombre)}${ult.fecha ? ` · del ${esc(ult.fecha.slice(0, 10))}` : ""}. Cuando cargue una campaña nueva, pasa sola a esa.</div>
        </div>`;
      return;
    }
    box.className = "ax-venta-detalle ax-venta-detalle--warn";
    box.innerHTML = `
      <div class="ax-vd-main">
        <div class="ax-vd-title">Sin campañas propias</div>
        <div class="ax-vd-sub">Este perfil no tiene ninguna campaña en el CRM, así que el tráfico se descuenta de la campaña por defecto de la cuenta.</div>
      </div>`;
    return;
  }

  const saldo = saldoDe(v);
  const bajo = saldo < VENTA_SALDO_BAJO;
  const sug = ventaSugerida(ig);
  const mejor = sug && sug.idventa !== v.idventa && saldoDe(sug) > saldo;
  box.className = "ax-venta-detalle" + (bajo ? " ax-venta-detalle--bad" : (!v.activa ? " ax-venta-detalle--warn" : ""));
  box.innerHTML = `
    <div class="ax-vd-main">
      <div class="ax-vd-title">${fmtSaldo(saldo)} disponibles
        ${v.activa ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Activa</span>'
                   : '<span class="ax-pill ax-pill--paused"><span class="ax-pdot"></span>Vieja</span>'}
      </div>
      <div class="ax-vd-sub">Campaña #${esc(v.idventa)} · ${esc(v.nombre)}${v.fecha ? ` · desde el ${esc(v.fecha.slice(0, 10))}` : ""}</div>
      ${bajo ? '<div class="ax-vd-alert">Casi sin saldo: el tráfico va a fallar. Pasalo a otra campaña.</div>' : ""}
      ${!bajo && !v.activa ? '<div class="ax-vd-alert">Ya no está entre las campañas activas del CRM.</div>' : ""}
    </div>
    ${mejor ? `<button type="button" class="ax-btn ax-btn--sm" onclick="usarVentaSugerida()">Pasar a #${esc(sug.idventa)} · ${fmtSaldo(saldoDe(sug))}</button>` : ""}`;
}

// Botón "usar la de más saldo": evita tener que leer toda la lista.
function usarVentaSugerida() {
  const ig = (document.getElementById("client-ig").value || "").trim().toLowerCase();
  const sug = ventaSugerida(ig);
  if (!sug) return;
  document.getElementById("client-venta").value = sug.idventa;
  actualizarVentaHint();
  updatePromptCount();   // refresca el aviso de cambios sin guardar
}

// El genérico es del sistema: se muestra solo al admin, siempre activo, sin
// borrar ni pausar ni renombrar. Lo único editable es el prompt (y género/rangos).
function genericCard(c) {
  return `
    <div class="ax-card">
      <div class="ax-avatar" style="${avatarStyle(c.ig_username)}">🌐</div>
      <div class="ax-main">
        <div class="ax-name">Genéricos (posts sin cliente)
          <span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Siempre activo</span>
        </div>
        <div class="ax-sub">Se aplica a todo post que no sea de un cliente cargado, en todos los vendedores
          · ${(c.prompt || "").length} car. de prompt</div>
      </div>
      <div class="ax-acts">
        <button class="ax-btn ax-btn--sm" onclick="openClientModal(${c.id})">Editar prompt</button>
      </div>
    </div>`;
}

function clientCard(c) {
  if (c.reserved) return genericCard(c);
  return `
    <div class="ax-card ${c.status === "paused" ? "ax-dimmed" : ""}">
      <div class="ax-avatar" style="${avatarStyle(c.ig_username)}">${esc(initials(c.display_name, c.ig_username))}</div>
      <div class="ax-main">
        <div class="ax-name">${esc(c.display_name) || '<span style="color:var(--faint)">(sin nombre)</span>'}
          ${c.status === "active"
            ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Activo</span>'
            : '<span class="ax-pill ax-pill--paused"><span class="ax-pdot"></span>Pausado</span>'}
          ${c.quality === "pro"
            ? '<span class="ax-pill ax-pill--pro" title="Corre con el modelo de IA más potente">Pro</span>'
            : ""}
        </div>
        <div class="ax-sub"><span class="ax-handle" title="Copiar" onclick="copyHandle('${esc(c.ig_username)}')">@${esc(c.ig_username)}</span>
          · ${(c.prompt || "").length} car. de prompt
          · ${ventaBadge(c)}</div>
      </div>
      <div class="ax-acts">
        <button class="ax-btn ax-btn--sm" onclick="openClientModal(${c.id})">Editar</button>
        ${IS_ADMIN ? "" : `<button class="ax-btn ax-btn--sm" title="Pedirle al administrador que ajuste el prompt" onclick="openPedidoModal(${c.id})">Pedir ajuste</button>`}
        <button class="ax-btn ax-btn--sm" onclick="togglePause(${c.id})">${c.status === "active" ? "Pausar" : "Activar"}</button>
        <button class="ax-btn ax-btn--sm ax-btn--danger ax-btn--icon" title="Borrar" onclick="deleteClient(${c.id})">
          <svg viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3h4v1M5 4l.5 9h5L11 4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
    </div>`;
}

// Resumen arriba de la lista: qué falta para que cada cliente pague lo suyo.
function renderFondosAlert() {
  const box = document.getElementById("fondos-alert");
  if (!box) return;
  const activos = clientsCache.filter(c => c.status === "active");
  const estados = activos.map(estadoFondos);
  const sinPropia = estados.filter(f => !f.venta).length;   // caen en la por defecto
  const flojos = estados.filter(f => f.venta && f.nivel !== "ok").length;
  if (!ventasCache.length || (!sinPropia && !flojos)) { box.classList.remove("ax-on"); return; }
  const partes = [];
  if (sinPropia) partes.push(`<b>${sinPropia}</b> sin campañas en el CRM: pagan de la campaña por defecto`);
  if (flojos) partes.push(`<b>${flojos}</b> con la campaña casi sin saldo o vencida`);
  box.innerHTML = `💰 ${partes.join(" · ")}.`;
  box.classList.add("ax-on");
}

function renderClients() {
  renderFondosAlert();
  const q = (document.getElementById("cli-search").value || "").trim().toLowerCase();
  const list = document.getElementById("clientes-list");
  const items = clientsCache.filter(c =>
    !q || c.ig_username.includes(q) || (c.display_name || "").toLowerCase().includes(q));
  // El genérico va siempre al final, en su propia sección y sin filtrar por el
  // buscador (es del sistema, no uno más de la lista).
  const sistema = genericCache
    ? `<div class="ax-group">🌐<span class="ax-group-t">Del sistema</span><span class="ax-group-c">1</span><span class="ax-group-line"></span></div>${genericCard(genericCache)}`
    : "";
  if (!clientsCache.length) { list.innerHTML = emptyState("Todavía no hay clientes", "Creá el primero con su @usuario y su prompt.") + sistema; return; }
  if (!items.length) { list.innerHTML = emptyState("Sin resultados", "Probá con otro nombre o @usuario.") + sistema; return; }

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
  list.innerHTML = html + sistema;
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
    v("client-ig"), v("client-name"), v("client-status"), v("client-gender"),
    v("client-quality"),
    v("client-venta"), v("client-prompt"),
    ...["likes", "views", "shares"].flatMap(k => [v(`range-${k}-min`), v(`range-${k}-max`)]),
  ]);
}

function clientIsDirty() { return _clientFormState() !== clientSnapshot; }

function updatePromptCount() {
  const txt = document.getElementById("client-prompt").value;
  const palabras = txt.trim() ? txt.trim().split(/\s+/).length : 0;
  const resumen = `${txt.length} caracteres · ${palabras} palabra${palabras === 1 ? "" : "s"}`;
  document.getElementById("client-prompt-count").textContent = resumen;
  document.getElementById("client-prompt-teaser-count").textContent =
    txt.trim() ? resumen : "Todavía sin instrucciones";
  document.getElementById("client-dirty").classList.toggle("ax-on", clientIsDirty());
}

// Pantalla completa del editor: esconde la columna de datos y deja el textarea
// a toda la ventana, para prompts largos.
function togglePromptFull() {
  const modal = document.querySelector("#client-mo .ax-modal");
  const full = modal.classList.toggle("ax-modal--full");
  modal.querySelector(".ax-editor-btn-ico").textContent = full ? "⤡" : "⤢";
  document.getElementById("client-prompt-expand-txt").textContent = full ? "Volver a los datos" : "Agrandar prompt";
  if (full) document.getElementById("client-prompt").focus();
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

function findClient(id) {
  if (genericCache && genericCache.id === id) return genericCache;
  return clientsCache.find(x => x.id === id) || null;
}

function openClientModal(id) {
  if (!selectedVendedor) { toast("Elegí un vendedor primero", "bad"); return; }
  hideErr("client-err");
  const c = id ? findClient(id) : null;
  const gen = !!(c && c.reserved);
  document.getElementById("client-title").textContent =
    gen ? "Prompt de los posts sin cliente" : (c ? "Editar cliente" : "Nuevo cliente");
  // Editando no hace falta bajada: el título ya dice todo.
  const sub = document.getElementById("client-subtitle");
  sub.textContent = gen
    ? "Es el prompt que se usa en TODOS los vendedores cuando el post no es de un cliente cargado. Se edita solo desde acá."
    : (c ? "" : "Se guarda en la base y el motor lo usa al instante, sin deploy.");
  sub.style.display = (c && !gen) ? "none" : "";
  document.getElementById("client-id").value = c ? c.id : "";
  document.getElementById("client-ig").value = c ? c.ig_username : "";
  document.getElementById("client-name").value = c ? c.display_name : "";
  document.getElementById("client-status").value = c ? c.status : "active";
  document.getElementById("client-gender").value = c && c.gender ? c.gender : "";
  // Cliente nuevo arranca en estándar: subir a pro es una decisión explícita.
  document.getElementById("client-quality").value = c && c.quality === "pro" ? "pro" : "standard";
  renderVentaSelect(c ? c.ig_username : document.getElementById("client-ig").value);
  document.getElementById("client-venta").value = c && c.crm_idventa ? c.crm_idventa : "";
  actualizarVentaHint();
  const rg = (c && c.ranges) || {};
  for (const k of ["likes", "views", "shares"]) {
    document.getElementById(`range-${k}-min`).value = rg[k] && rg[k].min != null ? rg[k].min : "";
    document.getElementById(`range-${k}-max`).value = rg[k] && rg[k].max != null ? rg[k].max : "";
  }
  // El prompt final del motor son dos capas: el genérico (reglas para todos) +
  // esto. Conviene que quede clarísimo cuál de las dos se está editando.
  const help = document.getElementById("client-prompt-help");
  if (help) {
    help.innerHTML = gen
      ? "Estas reglas se aplican a <b>todos los clientes</b>, arriba de las instrucciones propias de cada uno. Es el lugar para los arreglos generales (ej: que no todos los comentarios arranquen en minúscula)."
      : "Acá va <b>solo lo propio de este cliente</b> (rubro, personajes, @menciones, tono). Las reglas generales del prompt de sistema se le suman solas al generar — no hace falta repetirlas. Si algo se contradice, manda lo que escribas acá.";
  }
  document.getElementById("client-prompt").value = c ? c.prompt : "";
  // Cada ficha arranca sin propuesta de IA pendiente ni pedido asociado: el
  // "Deshacer" de un cliente no puede sobrevivir al abrir otro.
  aiPromptAnterior = null;
  pedidoEnCurso = null;
  const undo = document.getElementById("client-prompt-undo");
  if (undo) undo.classList.add("ax-hidden");
  document.querySelector("#client-mo .ax-modal").classList.remove("ax-modal--full");
  document.querySelector("#client-mo .ax-editor-btn-ico").textContent = "⤢";
  document.getElementById("client-prompt-expand-txt").textContent = "Agrandar prompt";
  // Del genérico solo se toca el prompt (y género/rangos): @usuario, nombre,
  // estado y campaña son del sistema y quedan bloqueados.
  for (const f of ["client-ig", "client-name", "client-status", "client-venta"]) {
    document.getElementById(f).disabled = gen;
  }
  clientSnapshot = _clientFormState();
  updatePromptCount();
  openMo("client-mo");
  // El prompt arranca oculto, así que el foco va siempre al primer dato.
  setTimeout(() => document.getElementById(gen ? "client-prompt" : "client-ig").focus(), 50);
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
    quality: document.getElementById("client-quality").value,
    ranges,
    prompt: document.getElementById("client-prompt").value,
    // El idvendedor viaja junto al idventa: el CRM imputa la orden a ese par, y
    // mezclar la venta de uno con el vendedor de otro la rechaza o la imputa mal.
    crm_idventa: document.getElementById("client-venta").value,
    crm_idvendedor: (ventaById(document.getElementById("client-venta").value) || {}).idvendedor || "",
  };
  const btn = document.getElementById("client-save"); btn.disabled = true;
  try {
    if (id) await api("PATCH", cliUrl(`/${id}`), payload);
    else await api("POST", cliUrl(), payload);
    clientSnapshot = _clientFormState();   // guardado ⇒ ya no hay cambios pendientes
    document.querySelector("#client-mo .ax-modal").classList.remove("ax-modal--full");
    closeMo("client-mo");
    toast(id ? "Cliente actualizado" : "Cliente creado", "ok");
    // Si veníamos de la bandeja, guardar el prompt ES resolver el pedido.
    if (pedidoEnCurso) { const pid = pedidoEnCurso; pedidoEnCurso = null; cerrarPedido(pid, "done"); }
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
  // Las solicitudes pendientes van arriba: son lo único que requiere una decisión.
  const orden = { pending: 0, approved: 1, rejected: 2 };
  items.sort((a, b) => (orden[a.status] ?? 1) - (orden[b.status] ?? 1) || a.name.localeCompare(b.name));
  list.innerHTML = items.map(v => {
    const pend = v.status === "pending";
    const pill = pend
      ? '<span class="ax-pill ax-pill--paused"><span class="ax-pdot"></span>Pendiente de habilitación</span>'
      : (v.active ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Activo</span>'
                  : '<span class="ax-pill ax-pill--off"><span class="ax-pdot"></span>Sin acceso</span>');
    const acts = pend
      ? `<button class="ax-btn ax-btn--sm ax-btn--primary" onclick="resolveVendor(${v.id}, 'approved')">Aceptar</button>
         <button class="ax-btn ax-btn--sm" onclick="resolveVendor(${v.id}, 'rejected')">Restringir</button>`
      : `<button class="ax-btn ax-btn--sm" onclick="openVendorModal(${v.id})">Editar</button>
         <button class="ax-btn ax-btn--sm" onclick="toggleVendorActive(${v.id})">${v.active ? "Restringir" : "Habilitar"}</button>`;
    const sub = pend
      ? `${esc(v.crm_email || "(sin email)")} · pidió acceso con sus credenciales de Growi`
      : `${esc(v.crm_email || "(sin email)")}${v.crm_idvendedor ? ` · id ${esc(v.crm_idvendedor)}` : ""}`;
    return `
    <div class="ax-card ${v.active || pend ? "" : "ax-dimmed"}">
      <div class="ax-avatar" style="${avatarStyle(v.name)}">${esc((v.name[0] || "?").toUpperCase())}</div>
      <div class="ax-main">
        <div class="ax-name">${esc(v.name)}
          ${pill}
          ${v.has_password || pend ? "" : '<span class="ax-pill ax-pill--paused" title="Sin contraseña guardada">sin pass</span>'}
        </div>
        <div class="ax-sub">${sub}</div>
      </div>
      <div class="ax-acts">${acts}</div>
    </div>`;
  }).join("");
}

async function resolveVendor(id, status) {
  const v = vendedoresCache.find(x => x.id === id); if (!v) return;
  const aprobar = status === "approved";
  const ok = await confirmDialog({
    title: aprobar ? "Habilitar vendedor" : "Restringir acceso",
    text: aprobar
      ? `${v.crm_email || v.name} va a poder entrar a la plataforma con sus credenciales de Growi.`
      : `${v.crm_email || v.name} no va a poder entrar. Podés habilitarlo más adelante.`,
    okLabel: aprobar ? "Habilitar" : "Restringir",
  });
  if (!ok) return;
  try {
    await api("PATCH", `/api/admin/vendedores/${id}/estado`, { status });
    toast(aprobar ? "Vendedor habilitado" : "Acceso restringido", "ok");
    await loadVendedores();
    loadClients();
  } catch (e) { toast(e.message, "bad"); }
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
      title: "Restringir acceso",
      text: `${v.name} no va a poder iniciar sesión hasta que lo habilites de nuevo.`,
      okLabel: "Restringir",
    });
    if (!ok) return;
  }
  try {
    await api("PATCH", `/api/admin/vendedores/${id}`, { active: !v.active });
    toast(v.active ? "Acceso restringido" : "Vendedor habilitado", "ok");
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

// ── Pedidos de ajuste de prompt ──────────────────────────────────────────────
// El vendedor manda texto plano (sin IA, cero tokens) y el admin resuelve la
// cola acá, disparando el asistente solo cuando se pone a trabajar el pedido.

let pedidosCache = [];

// --- Lado vendedor: abrir y enviar el pedido ---
function openPedidoModal(clientId) {
  const c = findClient(clientId);
  if (!c) return;
  hideErr("ped-err");
  document.getElementById("ped-client-id").value = clientId;
  document.getElementById("ped-title").textContent =
    `Pedir un ajuste · @${c.ig_username}`;
  document.getElementById("ped-text").value = "";
  openMo("ped-mo");
  setTimeout(() => document.getElementById("ped-text").focus(), 50);
}

async function enviarPedido() {
  const texto = document.getElementById("ped-text").value.trim();
  if (!texto) { showErr("ped-err", "Escribí qué querés cambiar"); return; }
  const btn = document.getElementById("ped-save"); btn.disabled = true;
  try {
    await api("POST", "/api/prompt-requests", {
      client_id: parseInt(document.getElementById("ped-client-id").value),
      text: texto,
    });
    closeMo("ped-mo");
    toast("Pedido enviado al administrador", "ok");
  } catch (e) { showErr("ped-err", e.message); } finally { btn.disabled = false; }
}

// --- Lado admin: la bandeja ---
function fechaCorta(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString("es-AR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

async function loadPedidos() {
  if (!IS_ADMIN) return;
  const sel = document.getElementById("ped-status");
  const status = sel ? sel.value : "pending";
  try {
    const d = await api("GET", `/api/prompt-requests${status ? `?status=${status}` : ""}`);
    pedidosCache = d.requests || [];
    renderPedidos();
    // El contador de la pestaña siempre muestra PENDIENTES, esté donde esté el
    // filtro: es el número que le importa al admin de un vistazo.
    if (status === "pending") setTxt("tab-ped-cnt", pedidosCache.length);
    else {
      const p = await api("GET", "/api/prompt-requests?status=pending");
      setTxt("tab-ped-cnt", (p.requests || []).length);
    }
  } catch (e) { toast(e.message, "bad"); }
}

function pedidoCard(p) {
  const pend = p.status === "pending";
  const pill = pend
    ? '<span class="ax-pill ax-pill--paused"><span class="ax-pdot"></span>Pendiente</span>'
    : p.status === "done"
    ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Resuelto</span>'
    : '<span class="ax-pill"><span class="ax-pdot"></span>Descartado</span>';
  const resuelto = p.resolved_at
    ? ` · ${p.status === "done" ? "resuelto" : "descartado"} ${fechaCorta(p.resolved_at)}${p.resolved_by ? " por " + esc(p.resolved_by) : ""}`
    : "";
  return `
    <div class="ax-card ${pend ? "" : "ax-dimmed"}" style="align-items:flex-start;">
      <div class="ax-avatar" style="${avatarStyle(p.client_ig_username)}">${esc(initials("", p.client_ig_username))}</div>
      <div class="ax-main">
        <div class="ax-name">@${esc(p.client_ig_username)} ${pill}</div>
        <div class="ax-sub">${esc(p.account_name || "")} · pedido por ${esc(p.username || "—")} · ${fechaCorta(p.created_at)}${resuelto}</div>
        <div class="ax-ped-txt">${esc(p.text)}</div>
      </div>
      <div class="ax-acts">
        ${p.client_id
          ? `<button class="ax-btn ax-btn--sm ax-btn--primary" onclick="trabajarPedido(${p.id})">Trabajar</button>`
          : '<span class="ax-hint">Cliente borrado</span>'}
        ${pend
          ? `<button class="ax-btn ax-btn--sm" onclick="cerrarPedido(${p.id},'done')">Marcar resuelto</button>
             <button class="ax-btn ax-btn--sm" onclick="cerrarPedido(${p.id},'discarded')">Descartar</button>`
          : `<button class="ax-btn ax-btn--sm" onclick="cerrarPedido(${p.id},'pending')">Reabrir</button>`}
      </div>
    </div>`;
}

function renderPedidos() {
  const list = document.getElementById("pedidos-list");
  if (!list) return;
  if (!pedidosCache.length) {
    list.innerHTML = emptyState("No hay pedidos acá",
      "Cuando un vendedor pida un ajuste de prompt, te aparece en esta bandeja.");
    return;
  }
  list.innerHTML = pedidosCache.map(pedidoCard).join("");
}

// "Trabajar" = abrir el cliente del pedido con el asistente de IA ya cargado con
// el texto del vendedor. Al guardar, el pedido se marca resuelto solo.
let pedidoEnCurso = null;

async function trabajarPedido(id) {
  const p = pedidosCache.find(x => x.id === id);
  if (!p || !p.client_id) return;
  // El cliente vive en la cuenta del vendedor que pidió: hay que pararse ahí
  // antes de abrir la ficha, o el PATCH iría contra la cuenta equivocada.
  if (selectedVendedor !== p.account_id) {
    selectedVendedor = p.account_id;
    const sel = document.getElementById("vendedor-select");
    if (sel) sel.value = String(p.account_id);
    await loadClients();
  }
  switchTab("clientes");
  if (!findClient(p.client_id)) {
    toast("No encontré ese cliente (¿lo borraron?)", "bad");
    return;
  }
  // openClientModal resetea el estado del asistente, así que el pedido en curso
  // se marca DESPUÉS de abrir la ficha.
  openClientModal(p.client_id);
  pedidoEnCurso = p.id;
  openAiModal(p.text);
}

async function cerrarPedido(id, status) {
  try {
    await api("PATCH", `/api/prompt-requests/${id}`, { status });
    toast(status === "done" ? "Pedido resuelto" : status === "discarded" ? "Pedido descartado" : "Pedido reabierto", "ok");
    if (pedidoEnCurso === id) pedidoEnCurso = null;
    loadPedidos();
  } catch (e) { toast(e.message, "bad"); }
}

// ── Asistente de IA (solo admin) ─────────────────────────────────────────────
// Reescribe el prompt del cliente abierto según una instrucción en castellano.
// NO guarda: deja la propuesta en el textarea para revisarla y guardarla a mano.

let aiPromptAnterior = null;   // para "Deshacer IA"

function openAiModal(textoInicial) {
  if (!IS_ADMIN) return;
  hideErr("ai-err");
  document.getElementById("ai-instruction").value = textoInicial || "";
  const sub = document.getElementById("ai-subtitle");
  sub.textContent = textoInicial
    ? "Este es el pedido del vendedor, tal cual lo escribió. Editalo si hace falta y generá la propuesta."
    : "Escribí en castellano qué querés cambiar. La propuesta queda en el editor: no se guarda hasta que apretés Guardar.";
  openMo("ai-mo");
  setTimeout(() => document.getElementById("ai-instruction").focus(), 50);
}

async function runPromptAi() {
  const instruccion = document.getElementById("ai-instruction").value.trim();
  if (!instruccion) { showErr("ai-err", "Escribí qué querés cambiar"); return; }
  const btn = document.getElementById("ai-run");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "Generando…";
  hideErr("ai-err");
  try {
    const actual = document.getElementById("client-prompt").value;
    const abierto = findClient(parseInt(document.getElementById("client-id").value));
    const d = await api("POST", "/api/admin/prompt-ai", {
      instruction: instruccion,
      prompt: actual,
      client_name: document.getElementById("client-name").value,
      // Editando el genérico se escribe el prompt entero; editando un cliente,
      // solo su capa (el genérico ya se le suma solo al generar).
      is_generic: !!(abierto && abierto.reserved),
    });
    aiPromptAnterior = actual;
    document.getElementById("client-prompt").value = d.prompt;
    document.getElementById("client-prompt-undo").classList.remove("ax-hidden");
    updatePromptCount();
    closeMo("ai-mo");
    toast("Propuesta lista — revisala y guardá", "ok");
  } catch (e) {
    showErr("ai-err", e.message);
  } finally { btn.disabled = false; btn.textContent = label; }
}

function undoAiPrompt() {
  if (aiPromptAnterior === null) return;
  document.getElementById("client-prompt").value = aiPromptAnterior;
  aiPromptAnterior = null;
  document.getElementById("client-prompt-undo").classList.add("ax-hidden");
  updatePromptCount();
  toast("Volví al prompt anterior", "ok");
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
    loadPedidos();
  } else {
    // Modo vendedor: solo sus clientes. selectedVendedor truthy para pasar los
    // guards; el backend usa la cuenta de la sesión (ignora el ?vendedor).
    selectedVendedor = MY_ACCOUNT || "me";
    loadClients();
  }
})();
