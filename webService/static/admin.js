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
function openMo(id) {
  const mo = document.getElementById(id);
  // Quién tenía el foco antes de abrir: al cerrar se lo devolvemos, si no el
  // tab arranca de cero arriba de la página.
  mo._focoPrevio = document.activeElement;
  mo.classList.add("ax-on");
}
function closeMo(id) {
  const mo = document.getElementById(id);
  mo.classList.remove("ax-on");
  const prev = mo._focoPrevio;
  mo._focoPrevio = null;
  // El botón que abrió el modal puede haber desaparecido (lista redibujada).
  if (prev && prev.isConnected && prev.focus) prev.focus();
}

// El tab no se escapa del modal abierto: con un modal a pantalla completa,
// tabular hasta el fondo dejaba el foco en la lista de atrás, invisible.
document.addEventListener("keydown", e => {
  if (e.key !== "Tab") return;
  const mo = [...document.querySelectorAll(".ax-mo.ax-on")].pop();
  if (!mo) return;
  const f = [...mo.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )].filter(el => el.offsetParent !== null || el === document.activeElement);
  if (!f.length) return;
  const primero = f[0], ultimo = f[f.length - 1];
  if (e.shiftKey && document.activeElement === primero) { e.preventDefault(); ultimo.focus(); }
  else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primero.focus(); }
});

// ── Elegir entre 2-3 opciones: botones en vez de <select> ──
// El <select> sigue siendo el dueño del valor (todo el resto del archivo lo lee
// y lo escribe con .value); esto solo le dibuja botones al lado y los mantiene
// sincronizados en los dos sentidos.
function buildSegs(root) {
  for (const sel of root.querySelectorAll("select[data-seg]")) {
    if (sel.classList.contains("ax-seg-src")) { sincronizarSeg(sel); continue; }
    sel.classList.add("ax-seg-src");
    sel.tabIndex = -1;
    const grupo = document.createElement("div");
    grupo.className = "ax-opt" + (sel.hasAttribute("data-seg-wide") ? " ax-opt--wide" : "");
    grupo.setAttribute("role", "radiogroup");
    const etiqueta = sel.closest(".ax-field")?.querySelector("label");
    if (etiqueta) grupo.setAttribute("aria-label", etiqueta.textContent.trim());
    for (const op of sel.options) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ax-opt-b";
      b.setAttribute("role", "radio");
      b.dataset.val = op.value;
      const ico = op.dataset.ico ? `<span class="ax-opt-ico" aria-hidden="true">${esc(op.dataset.ico)}</span>` : "";
      const txt = esc(op.dataset.short || op.textContent);
      const desc = op.dataset.desc ? `<span class="ax-opt-desc">${esc(op.dataset.desc)}</span>` : "";
      b.innerHTML = `${ico}<span class="ax-opt-txt">${txt}</span>${desc}`;
      b.onclick = () => {
        if (sel.disabled) return;
        sel.value = op.value;
        // Bubbling a mano: el select cambió por código, así que nadie se entera
        // solo. El listener delegado de "cambios sin guardar" escucha esto.
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        sincronizarSeg(sel);
      };
      grupo.appendChild(b);
    }
    sel.insertAdjacentElement("afterend", grupo);
    sel._seg = grupo;
    sincronizarSeg(sel);
  }
}

// Refleja en los botones lo que diga el <select> (valor y disabled).
function sincronizarSeg(sel) {
  const grupo = sel._seg;
  if (!grupo) return;
  grupo.classList.toggle("ax-opt--off", sel.disabled);
  for (const b of grupo.children) {
    const on = b.dataset.val === sel.value;
    b.setAttribute("aria-checked", on ? "true" : "false");
    // Solo la opción elegida entra en el tab: dentro de un radiogroup se navega
    // con las flechas, no tabulando opción por opción.
    b.tabIndex = on ? 0 : -1;
  }
}

// Flechas dentro del grupo, como manda un radiogroup.
document.addEventListener("keydown", e => {
  const b = e.target.closest?.(".ax-opt-b");
  if (!b || !["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.key)) return;
  e.preventDefault();
  const hnos = [...b.parentElement.children];
  const paso = (e.key === "ArrowRight" || e.key === "ArrowDown") ? 1 : -1;
  const sig = hnos[(hnos.indexOf(b) + paso + hnos.length) % hnos.length];
  sig.click();
  sig.focus();
});

document.addEventListener("click", e => {
  if (!e.target.classList.contains("ax-mo")) return;
  // El de cliente pasa por su propia guarda de cambios sin guardar.
  if (e.target.id === "client-mo") { closeClientModal(); return; }
  e.target.classList.remove("ax-on");
});
document.addEventListener("keydown", e => {
  const enClienteEditor = document.getElementById("client-mo").classList.contains("ax-on");
  const modal = document.querySelector("#client-mo .ax-modal");

  // ⌘/Ctrl + S: el reflejo de cualquiera que escribe un texto largo. Sin esto el
  // navegador abre "Guardar página como…" arriba del modal.
  if (enClienteEditor && (e.key === "s" || e.key === "S") && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    document.getElementById("client-save").click();
    return;
  }
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
      setFieldErr("client-ig", validarIg(e.target.value));
      // Cliente nuevo: se propone el nombre a partir del @usuario hasta que el
      // vendedor escriba uno propio (ahí no se toca más).
      const nom = document.getElementById("client-name");
      if (!document.getElementById("client-id").value && !nom.dataset.tocado)
        nom.value = nombreDesdeIg(e.target.value);
      pintarCabeceraCliente();
    }
    if (e.target.id === "client-name") {
      document.getElementById("client-name").dataset.tocado = "1";
      pintarCabeceraCliente();
    }
    if (e.target.id === "client-status") pintarCabeceraCliente();
    // Los rangos se validan mientras se tipea: mín > máx o calidad sin rango se
    // ven al toque, no al apretar Guardar.
    if (e.target.closest(".ax-range-row")) revisarRangos();
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

// Error pegado al campo que lo causó: el cartel rojo de arriba del modal no
// decía cuál de los ocho campos estaba mal.
function setFieldErr(inputId, msg) {
  const fe = document.getElementById("fe-" + inputId);
  const input = document.getElementById(inputId);
  if (fe) { fe.textContent = msg || ""; fe.classList.toggle("ax-on", !!msg); }
  if (input) {
    input.classList.toggle("ax-bad", !!msg);
    input.setAttribute("aria-invalid", msg ? "true" : "false");
  }
}
function clearFieldErrs(scopeId) {
  const box = document.getElementById(scopeId);
  if (!box) return;
  box.querySelectorAll(".ax-fe").forEach(e => { e.textContent = ""; e.classList.remove("ax-on"); });
  box.querySelectorAll(".ax-bad").forEach(e => { e.classList.remove("ax-bad"); e.removeAttribute("aria-invalid"); });
}
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
// Fichas del sistema (genérico, palabra clave). Son varias: el backend manda
// cada una con reserved:true y sus textos (system_icon/desc/title/sub).
let sistemaCache = [];

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
    sistemaCache = clients.filter(c => c.reserved);
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
      <div class="ax-avatar" style="${avatarStyle(c.ig_username)}">${c.system_icon || "🌐"}</div>
      <div class="ax-main">
        <div class="ax-name">${esc(c.display_name)}
          <span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Siempre activo</span>
        </div>
        <div class="ax-sub">${esc(c.system_desc || "")}
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
  const sistema = sistemaCache.length
    ? `<div class="ax-group">🌐<span class="ax-group-t">Del sistema</span><span class="ax-group-c">${sistemaCache.length}</span><span class="ax-group-line"></span></div>${sistemaCache.map(genericCard).join("")}`
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
// Tipos de producto con rango min-max configurable por cliente (TAREA 6).
const RANGE_KEYS = ["likes", "views", "shares", "reposts", "saves", "reach"];

// ── Calidades por tipo de producto ───────────────────────────────────────────
// El CRM ofrece varias variantes del mismo tipo ("Likes" vs "Likes 1178 JAP"):
// esa es la CALIDAD. Acá se fija cuál usa este cliente cuando la herramienta
// precrea la orden; vacío = la base, como venía siendo.
let _prodsPorTipo = null;   // {likes: [{id, nombre}], ...} — cache de la sesión

// Espejo de _tipoProducto() de app.js y de _tipo_producto() del backend: los
// tres tienen que clasificar igual o la calidad elegida no matchea nada.
function _tipoProducto(nombre) {
  const n = (nombre || "").toLowerCase();
  if (n.includes("like") || n.includes("me gusta")) return "likes";
  if (n.includes("view") || n.includes("reproduc") || n.includes("visualiz") || n.includes("vista")) return "views";
  if (n.includes("repost") || n.includes("reposte") || n.includes("requeteo")) return "reposts";
  if (n.includes("save") || n.includes("guardad") || n.includes("guardar")) return "saves";
  if (n.includes("reach") || n.includes("alcance")) return "reach";
  if (n.includes("share") || n.includes("compart")) return "shares";
  return null;
}

async function cargarCalidades() {
  if (_prodsPorTipo) return _prodsPorTipo;
  const out = {};
  for (const k of RANGE_KEYS) out[k] = [];
  try {
    const data = await api("GET", "/api/productos?rrss=1");   // 1 = Instagram
    for (const items of Object.values(data || {})) {
      for (const p of items || []) {
        const t = _tipoProducto(p.label);
        if (t && out[t]) out[t].push({ id: String(p.id), nombre: p.label });
      }
    }
  } catch (e) {
    console.error("cargarCalidades:", e);   // sin CRM se sigue: quedan los rangos solos
  }
  _prodsPorTipo = out;
  return out;
}

// Espaciado por defecto entre tandas cuando la orden se divide: 2 horas
// (espeja el backend).
const DRIP_CADA_DEFAULT = 120;

// Un color por tipo: el ojo agarra la tarjeta correcta sin leer el nombre.
const RANGE_COLORS = {
  likes: "#ff6b6b", views: "#7dd3fc", shares: "#86efac",
  reposts: "#c4b5fd", saves: "#fbcfe8", reach: "#fdba74",
};

const RANGE_LABELS = {
  likes: "👍 Likes", views: "▶ Views", shares: "↗ Shares",
  reposts: "🔁 Reposts", saves: "🔖 Saves", reach: "📡 Reach",
};

// Estado de los rangos de la ficha abierta: {likes: [{min,max,prod_id,prod_nombre}], ...}
// Un tipo puede tener VARIAS entradas: hay clientes que piden dos calidades de
// likes en el mismo post, cada una con su rango.
let rangosState = {};

function _entradasDeRango(v) {
  if (Array.isArray(v)) return v;
  return v ? [v] : [];          // fichas viejas: un solo objeto por tipo
}

// Lee lo tipeado en pantalla. Es la fuente de verdad antes de agregar/sacar una
// fila (si no, se perdería lo escrito al re-renderizar).
function leerRangosDOM() {
  const out = {};
  for (const k of RANGE_KEYS) {
    const filas = document.querySelectorAll(`.ax-range-row[data-tipo="${k}"]`);
    out[k] = Array.from(filas).map(f => {
      const sel = f.querySelector(".ax-range-prod");
      const drip = f.querySelector(".ax-drip-n");
      const cada = f.querySelector(".ax-drip-min");
      return {
        min: f.querySelector(".ax-range-min").value,
        max: f.querySelector(".ax-range-max").value,
        prod_id: sel ? sel.value : "",
        prod_nombre: sel && sel.value ? (sel.options[sel.selectedIndex]?.text || "") : "",
        split: drip ? parseInt(drip.value) || 1 : 1,
        cada_min: cada ? parseInt(cada.value) || DRIP_CADA_DEFAULT : DRIP_CADA_DEFAULT,
      };
    });
  }
  return out;
}

// Dibuja el bloque de rangos: arriba los 6 tipos como chips (se prenden y se
// apagan) y abajo solo los que están prendidos. Antes se mostraban los 6
// siempre, con su fila y su división en tandas: media pantalla de campos vacíos para un
// cliente que suele tener dos o tres productos.
function renderRangos() {
  const box = document.getElementById("ranges-box");
  if (!box) return;
  const prods = _prodsPorTipo || {};
  const activos = RANGE_KEYS.filter(k => (rangosState[k] || []).length);

  let html = '<div class="ax-tipos">';
  for (const k of RANGE_KEYS) {
    const on = activos.includes(k);
    const n = (rangosState[k] || []).length;
    html += `<button type="button" class="ax-chip${on ? " ax-chip--on" : ""}" onclick="toggleTipo('${k}')"
      aria-pressed="${on}" style="--c:${RANGE_COLORS[k]}"
      title="${on ? "Sacar" : "Configurar"} ${esc(RANGE_LABELS[k])}">
      <i class="ax-chip-dot"></i>${RANGE_LABELS[k]}${n > 1 ? `<b class="ax-chip-n">${n}</b>` : ""}
    </button>`;
  }
  html += "</div>";

  if (_prodsPorTipo && !RANGE_KEYS.some(k => (prods[k] || []).length))
    html += '<div class="ax-range-note">No se pudieron traer los productos del CRM, así que no hay calidades para elegir. Los rangos se guardan igual.</div>';

  if (!activos.length) {
    html += '<div class="ax-range-empty">Ningún producto con cantidad automática. Tocá uno de arriba para que la herramienta le arme la orden sola.</div>';
    box.innerHTML = html;
    revisarRangos();
    return;
  }

  for (const k of activos) {
    const lista = prods[k] || [];
    const entradas = rangosState[k];
    html += `<div class="ax-range-group" data-tipo="${k}" style="--c:${RANGE_COLORS[k]}">
      <div class="ax-range-ghead">
        <span class="ax-range-name">${RANGE_LABELS[k]}</span>
        <span class="ax-range-resumen"></span>
        ${lista.length >= 2
          ? `<button type="button" class="ax-range-add" onclick="agregarCalidad('${k}')">+ otra calidad</button>`
          : ""}
        <button type="button" class="ax-range-del" title="Sacar ${esc(RANGE_LABELS[k])}" onclick="toggleTipo('${k}')">×</button>
      </div>`;
    entradas.forEach((e, i) => {
      const actual = e.prod_id ? String(e.prod_id) : "";
      let opts = '<option value="">Calidad automática</option>';
      for (const p of lista) opts += `<option value="${esc(p.id)}"${p.id === actual ? " selected" : ""}>${esc(p.nombre)}</option>`;
      // La calidad guardada puede haber desaparecido del CRM: la dejamos a la
      // vista en vez de resetearla a automática en silencio.
      if (actual && !lista.some(p => p.id === actual))
        opts += `<option value="${esc(actual)}" selected>${esc(e.prod_nombre || "#" + actual)} (ya no está en el CRM)</option>`;
      const mostrarSel = lista.length >= 2 || actual || entradas.length > 1;
      const split = [3, 5].includes(Number(e.split)) ? Number(e.split) : 1;
      const cada = e.cada_min ? Number(e.cada_min) : DRIP_CADA_DEFAULT;
      html += `<div class="ax-range-row" data-tipo="${k}">
        <input type="number" min="0" class="ax-range-min" value="${esc(e.min ?? "")}" placeholder="mín" aria-label="Mínimo de ${esc(RANGE_LABELS[k])}" />
        <span class="ax-range-dash">–</span>
        <input type="number" min="0" class="ax-range-max" value="${esc(e.max ?? "")}" placeholder="máx" aria-label="Máximo de ${esc(RANGE_LABELS[k])}" />
        <select class="ax-range-prod${mostrarSel ? "" : " ax-hidden"}" aria-label="Calidad de ${esc(RANGE_LABELS[k])}">${opts}</select>
        ${entradas.length > 1
          ? `<button type="button" class="ax-range-del" title="Sacar esta calidad" aria-label="Sacar esta calidad" onclick="quitarCalidad('${k}',${i})">×</button>`
          : "<span></span>"}
        <div class="ax-drip">
          <select class="ax-drip-n" aria-label="Cómo se manda ${esc(RANGE_LABELS[k])}">
            <option value="1"${split === 1 ? " selected" : ""}>⚡ Todo junto</option>
            <option value="3"${split === 3 ? " selected" : ""}>✂ Dividido ×3</option>
            <option value="5"${split === 5 ? " selected" : ""}>✂ Dividido ×5</option>
          </select>
          <span class="ax-drip-cada${split === 1 ? " ax-hidden" : ""}">
            cada <input type="number" min="5" max="1440" class="ax-drip-min" value="${esc(cada)}" aria-label="Minutos entre tandas" /> min
            <em class="ax-drip-total"></em>
          </span>
        </div>
        <div class="ax-fe ax-range-fe"></div>
      </div>`;
    });
    html += "</div>";
  }
  box.innerHTML = html;
  revisarRangos();
}

// Prende o apaga un tipo de producto. Apagarlo borra lo que tuviera cargado:
// es lo mismo que dejarlo vacío, y así el chip dice la verdad.
function toggleTipo(tipo) {
  rangosState = leerRangosDOM();
  if ((rangosState[tipo] || []).length) rangosState[tipo] = [];
  else rangosState[tipo] = [{}];
  renderRangos();
  const primero = document.querySelector(`.ax-range-group[data-tipo="${tipo}"] .ax-range-min`);
  if (primero) primero.focus();
  updatePromptCount();   // refresca el aviso de cambios sin guardar
}

// "90 min" / "2 h" / "2,5 h": el intervalo en la unidad que se lee mejor.
function _fmtCada(min) {
  if (min < 60) return `${min} min`;
  const h = min / 60;
  return `${(h % 1 ? h.toFixed(1) : h).toString().replace(".", ",")} h`;
}

// Valida las filas en vivo y actualiza el resumen del encabezado. Devuelve true
// si no hay ningún error que impida guardar.
function revisarRangos() {
  let ok = true, ordenes = 0;
  for (const fila of document.querySelectorAll(".ax-range-row")) {
    const min = fila.querySelector(".ax-range-min");
    const max = fila.querySelector(".ax-range-max");
    const sel = fila.querySelector(".ax-range-prod");
    const fe = fila.querySelector(".ax-range-fe");
    const tieneMin = min.value !== "", tieneMax = max.value !== "";
    let msg = "", warn = false;

    // El "cada N min" solo tiene sentido si la orden se divide en tandas, y se
    // muestra en horas para que se entienda cuánto dura el goteo.
    const drip = fila.querySelector(".ax-drip-n");
    const n = drip ? parseInt(drip.value) || 1 : 1;
    const cadaEl = fila.querySelector(".ax-drip-min");
    const cadaWrap = fila.querySelector(".ax-drip-cada");
    if (cadaWrap) cadaWrap.classList.toggle("ax-hidden", n === 1);
    if (n > 1 && cadaEl) {
      const cada = parseInt(cadaEl.value);
      if (!cada || cada < 5 || cada > 1440) {
        msg = "El espaciado va entre 5 y 1440 minutos.";
        cadaEl.classList.add("ax-bad");
      } else {
        cadaEl.classList.remove("ax-bad");
        const horas = ((n - 1) * cada) / 60;
        const tot = fila.querySelector(".ax-drip-total");
        if (tot) tot.textContent = `· termina en ${_fmtCada((n - 1) * cada)}`;
      }
    }

    if (msg) {
      // ya hay error de la división en tandas en esta fila
    } else if (tieneMin !== tieneMax) {
      msg = "Faltan los dos números: sin mínimo y máximo no se autocompleta nada.";
    } else if (tieneMin && parseInt(min.value) > parseInt(max.value)) {
      msg = "El mínimo es mayor que el máximo.";
    } else if (tieneMin && parseInt(max.value) === 0) {
      msg = "El máximo tiene que ser mayor que cero.";
    } else if (!tieneMin && sel && sel.value) {
      // No rompe el guardado, pero la calidad elegida se pierde: hay que decirlo
      // acá y no recién en un toast al apretar Guardar.
      msg = "Elegiste calidad pero no cargaste el rango: así no se guarda.";
      warn = true;
    }

    fe.textContent = msg;
    fe.classList.toggle("ax-on", !!msg);
    fe.classList.toggle("ax-fe--warn", warn);
    const malo = !!msg && !warn;
    min.classList.toggle("ax-bad", malo);
    max.classList.toggle("ax-bad", malo);
    if (malo) ok = false;
    if (!msg && tieneMin) ordenes += n;
  }
  // Resumen vivo de cada tarjeta: lo que va a hacer la herramienta, en criollo.
  for (const g of document.querySelectorAll(".ax-range-group")) {
    const cargado = Array.from(g.querySelectorAll(".ax-range-min")).some(i => i.value !== "");
    g.classList.toggle("ax-range-group--on", cargado);
    const res = g.querySelector(".ax-range-resumen");
    if (!res) continue;
    const partes = [];
    for (const fila of g.querySelectorAll(".ax-range-row")) {
      const mn = fila.querySelector(".ax-range-min").value;
      const mx = fila.querySelector(".ax-range-max").value;
      if (mn === "" || mx === "") continue;
      const n = parseInt(fila.querySelector(".ax-drip-n").value) || 1;
      const cada = parseInt(fila.querySelector(".ax-drip-min").value) || DRIP_CADA_DEFAULT;
      const num = v => Number(v).toLocaleString("es-AR");
      partes.push(`${num(mn)}–${num(mx)}` + (n > 1 ? ` en ${n} partes cada ${_fmtCada(cada)}` : ""));
    }
    res.textContent = partes.join("  ·  ");
  }
  const resumen = document.getElementById("ranges-resumen");
  if (resumen)
    resumen.textContent = ordenes
      ? `${ordenes} orden${ordenes === 1 ? "" : "es"} se van a precrear solas`
      : "sin rangos: la cantidad se carga a mano";
  return ok;
}

// ── Comentarios por post ─────────────────────────────────────────────────────
// Cuántos comentarios de cada tipo manda este cliente. No es un RANGE_KEY: no
// precrea ninguna orden, solo autocompleta los dos casilleros de la herramienta.
// Tope de comunes: salen en dos tandas de 40 (espeja _COMENTARIOS_TOPE del backend).
const COM_TOPE = { verificados: null, comunes: 80 };

const _comEl = (tipo, cual) =>
  document.getElementById(`com-${tipo === "verificados" ? "verif" : "comunes"}-${cual}`);

function leerComentariosDOM() {
  const out = {};
  for (const k of ["verificados", "comunes"]) {
    out[k] = { min: _comEl(k, "min").value, max: _comEl(k, "max").value };
  }
  return out;
}

function renderComentarios(com) {
  const c = com || {};
  for (const k of ["verificados", "comunes"]) {
    const e = c[k] || {};
    _comEl(k, "min").value = e.min ?? "";
    _comEl(k, "max").value = e.max ?? "";
  }
  revisarComentarios();
}

// Valida las dos filas y actualiza el resumen. Devuelve false si algo impide guardar.
function revisarComentarios() {
  const fe = document.getElementById("com-fe");
  if (!fe) return true;
  let msg = "";
  const partes = [];
  for (const k of ["verificados", "comunes"]) {
    const min = _comEl(k, "min"), max = _comEl(k, "max");
    const tieneMin = min.value !== "", tieneMax = max.value !== "";
    let malo = "";
    if (tieneMin !== tieneMax) {
      malo = "Faltan los dos números: sin mínimo y máximo no se autocompleta nada.";
    } else if (tieneMin && parseInt(min.value) > parseInt(max.value)) {
      malo = "El mínimo es mayor que el máximo.";
    } else if (tieneMin && parseInt(max.value) === 0) {
      malo = "El máximo tiene que ser mayor que cero.";
    } else if (tieneMin && COM_TOPE[k] && parseInt(max.value) > COM_TOPE[k]) {
      malo = `Los comunes salen en dos tandas de 40: el tope es ${COM_TOPE[k]} por día.`;
    }
    min.classList.toggle("ax-bad", !!malo);
    max.classList.toggle("ax-bad", !!malo);
    if (malo && !msg) msg = malo;
    if (!malo && tieneMin) partes.push(`${min.value}–${max.value} ${k}`);
  }
  fe.textContent = msg;
  fe.classList.toggle("ax-on", !!msg);
  const resumen = document.getElementById("com-resumen");
  if (resumen)
    resumen.textContent = partes.length
      ? partes.join("  ·  ")
      : "sin cantidad fija: se carga a mano";
  return !msg;
}

function agregarCalidad(tipo) {
  rangosState = leerRangosDOM();
  (rangosState[tipo] = rangosState[tipo] || []).push({});
  renderRangos();
}

function quitarCalidad(tipo, i) {
  rangosState = leerRangosDOM();
  rangosState[tipo].splice(i, 1);
  renderRangos();
}

// Trae las calidades del CRM y redibuja con ellas. Se llama al abrir la ficha.
async function renderCalidades(rg) {
  rangosState = {};
  for (const k of RANGE_KEYS) rangosState[k] = _entradasDeRango(rg[k]);
  renderRangos();                 // primero sin calidades: los rangos ya se ven
  await cargarCalidades();
  renderRangos();
  // Los selects se llenan async, después de que openClientModal tomó la foto
  // del formulario: sin esto la ficha arranca marcada como "con cambios".
  clientSnapshot = _clientFormState();
  updatePromptCount();   // la foto cambió ⇒ el botón de guardar se resincroniza
}

let clientSnapshot = "";

function _clientFormState() {
  const v = (id) => document.getElementById(id).value;
  return JSON.stringify([
    v("client-ig"), v("client-name"), v("client-status"), v("client-gender"),
    v("client-quality"), document.getElementById("client-keyword-mode").checked,
    v("client-venta"), v("client-prompt"),
    leerRangosDOM(), leerComentariosDOM(),
  ]);
}

function clientIsDirty() { return _clientFormState() !== clientSnapshot; }

// El @usuario es la clave del cliente: el motor busca por ahí el prompt del post.
// Un espacio o una mayúscula de más y el post deja de matchear.
function validarIg(v) {
  const ig = (v || "").trim().replace(/^@/, "");
  if (!ig) return "Poné el @usuario de Instagram: es con lo que el motor reconoce los posts.";
  if (!/^[a-zA-Z0-9._]+$/.test(ig)) return "Solo letras, números, punto y guion bajo (sin espacios ni @).";
  if (ig.length > 30) return "Instagram no permite más de 30 caracteres.";
  // Dos clientes con el mismo @usuario dejan el motor sin saber qué prompt usar.
  // El backend lo rechaza, pero avisarlo acá evita perder lo escrito en un 400.
  const yo = document.getElementById("client-id").value;
  const clon = [...clientsCache, ...sistemaCache].find(
    x => String(x.id) !== String(yo) &&
      (x.ig_username || "").trim().toLowerCase() === ig.toLowerCase()
  );
  if (clon) return `Ya existe un cliente con @${ig}${clon.display_name ? ` (${clon.display_name})` : ""}.`;
  return "";
}

// "peter.fournier" ⇒ "Peter Fournier": el nombre para mostrar casi siempre es el
// @usuario prolijo, y escribirlo dos veces es trabajo de más.
function nombreDesdeIg(ig) {
  return (ig || "").trim().replace(/^@/, "").split(/[._-]+/).filter(Boolean)
    .map(p => p[0].toUpperCase() + p.slice(1)).join(" ");
}

// Avatar, título y estado de la cabecera, en vivo mientras se completa la ficha.
function pintarCabeceraCliente() {
  const ig = (document.getElementById("client-ig").value || "").trim().replace(/^@/, "");
  const nombre = (document.getElementById("client-name").value || "").trim();
  const av = document.getElementById("client-av");
  const sub = document.getElementById("client-subtitle");
  if (!av) return;
  if (!ig && !nombre) {
    av.textContent = "+";
    av.style.cssText = "background:var(--surface3);color:var(--muted)";
    return;
  }
  av.textContent = initials(nombre, ig);
  av.style.cssText = avatarStyle(ig || nombre);
  const pausado = document.getElementById("client-status").value === "paused";
  if (sub && sub.dataset.vivo === "1")
    sub.innerHTML = `<span style="font-family:ui-monospace,Menlo,monospace">@${esc(ig || "…")}</span>` +
      (pausado ? ' · <span class="ax-pill ax-pill--paused"><span class="ax-pdot"></span>Pausado</span>' : "");
}

function updatePromptCount() {
  const txt = document.getElementById("client-prompt").value;
  const palabras = txt.trim() ? txt.trim().split(/\s+/).length : 0;
  const resumen = `${txt.length} caracteres · ${palabras} palabra${palabras === 1 ? "" : "s"}`;
  document.getElementById("client-prompt-count").textContent = resumen;
  document.getElementById("client-prompt-teaser-count").textContent =
    txt.trim() ? resumen : "Todavía sin instrucciones";
  document.querySelector("#client-mo .ax-prompt-teaser")
    ?.classList.toggle("ax-prompt-teaser--empty", !txt.trim());
  // Palabra clave prendida ⇒ este prompt no se usa: se atenúa el textarea y se
  // marca la tarjeta cerrada, que si no no lo dice.
  const kw = document.getElementById("client-keyword-mode").checked;
  document.querySelector("#client-mo .ax-field--editor").classList.toggle("ax-kw-on", kw);
  document.getElementById("client-prompt-teaser-kw").classList.toggle("ax-hidden", !kw);
  const sucio = clientIsDirty();
  document.getElementById("client-dirty").classList.toggle("ax-on", sucio);
  // Editando sin tocar nada no hay nada que guardar: el botón lo dice en vez de
  // mandar un PATCH idéntico a lo que ya está en la base.
  const btn = document.getElementById("client-save");
  if (btn && !btn.classList.contains("ax-btn--busy")) {
    const editando = !!document.getElementById("client-id").value;
    btn.disabled = editando && !sucio;
    btn.textContent = !editando ? "Crear cliente" : (sucio ? "Guardar cambios" : "Sin cambios");
  }
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

// El guard de cerrar el modal no cubre cerrar la pestaña o recargar: un prompt
// largo a medio escribir se perdía sin una sola advertencia.
window.addEventListener("beforeunload", e => {
  if (!document.getElementById("client-mo")?.classList.contains("ax-on")) return;
  if (!clientIsDirty()) return;
  e.preventDefault();
  e.returnValue = "";
});

function findClient(id) {
  return sistemaCache.find(x => x.id === id)
    || clientsCache.find(x => x.id === id) || null;
}

function openClientModal(id) {
  if (!selectedVendedor) { toast("Elegí un vendedor primero", "bad"); return; }
  hideErr("client-err");
  const c = id ? findClient(id) : null;
  const gen = !!(c && c.reserved);
  document.getElementById("client-title").textContent =
    gen ? (c.system_title || "Prompt del sistema") : (c ? "Editar cliente" : "Nuevo cliente");
  // Editando no hace falta bajada: el título ya dice todo.
  const sub = document.getElementById("client-subtitle");
  sub.textContent = gen
    ? (c.system_sub || "")
    : (c ? "" : "Se guarda en la base y el motor lo usa al instante, sin deploy.");
  // Editando, la bajada muestra el @usuario y el estado en vivo en vez de un
  // texto explicativo que ya no hace falta.
  sub.dataset.vivo = (c && !gen) ? "1" : "0";
  sub.style.display = gen ? "" : "";
  clearFieldErrs("client-mo");
  document.getElementById("client-id").value = c ? c.id : "";
  document.getElementById("client-ig").value = c ? c.ig_username : "";
  const nomEl = document.getElementById("client-name");
  nomEl.value = c ? c.display_name : "";
  delete nomEl.dataset.tocado;   // ficha nueva ⇒ vuelve a sugerirse desde el @usuario
  document.getElementById("client-status").value = c ? c.status : "active";
  document.getElementById("client-gender").value = c && c.gender ? c.gender : "";
  // Cliente nuevo arranca en estándar: subir a pro es una decisión explícita.
  document.getElementById("client-quality").value = c && c.quality === "pro" ? "pro" : "standard";
  document.getElementById("client-keyword-mode").checked = !!(c && c.keyword_mode);
  // Las fichas del sistema no son un cliente: no tienen posts propios.
  document.getElementById("client-keyword-field").classList.toggle("ax-hidden", gen);
  renderVentaSelect(c ? c.ig_username : document.getElementById("client-ig").value);
  document.getElementById("client-venta").value = c && c.crm_idventa ? c.crm_idventa : "";
  actualizarVentaHint();
  const rg = (c && c.ranges) || {};
  renderComentarios(rg.comentarios);   // antes de renderCalidades: entra en el snapshot
  renderCalidades(rg);   // async: dibuja las filas y las llena con lo del CRM
  const av = document.getElementById("client-av");
  if (gen) {
    // El genérico no es una persona: ícono, no iniciales.
    av.textContent = "✎";
    av.style.cssText = "background:var(--yellow-lo);color:var(--yellow)";
  } else {
    pintarCabeceraCliente();
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
  // Los botones de estado/género/calidad se dibujan una vez y después solo se
  // resincronizan con lo que quedó cargado en cada <select>.
  buildSegs(document.getElementById("client-mo"));
  // Por qué están grises esos campos: sin decirlo parecía que la app se colgó.
  document.getElementById("client-gen-note").classList.toggle("ax-hidden", !gen);
  clientSnapshot = _clientFormState();
  updatePromptCount();
  openMo("client-mo");
  // El prompt arranca oculto, así que el foco va siempre al primer dato.
  setTimeout(() => document.getElementById(gen ? "client-prompt" : "client-ig").focus(), 50);
}

async function saveClient() {
  const id = document.getElementById("client-id").value;
  const gen = !!(id && (findClient(parseInt(id)) || {}).reserved);
  // Validación antes de salir a la red: el error aparece pegado al campo y el
  // foco va al primero que está mal, en vez de un 400 genérico arriba de todo.
  hideErr("client-err");
  const errIg = gen ? "" : validarIg(document.getElementById("client-ig").value);
  setFieldErr("client-ig", errIg);
  const rangosOk = revisarRangos();
  const comOk = revisarComentarios();
  if (errIg || !rangosOk || !comOk) {
    const primero = document.querySelector("#client-mo .ax-bad");
    if (primero) { primero.focus(); primero.scrollIntoView({ block: "center", behavior: "smooth" }); }
    showErr("client-err", errIg
      ? "Revisá el @usuario para poder guardar."
      : "Revisá los números marcados en rojo para poder guardar.");
    return;
  }
  // Lo que se guarda es lo que queda a la vista: se limpia el campo, no solo el
  // payload, para que la ficha no muestre algo distinto a lo que fue a la base.
  if (!gen) {
    const igEl = document.getElementById("client-ig");
    igEl.value = igEl.value.trim().replace(/^@/, "");
    const nomEl = document.getElementById("client-name");
    nomEl.value = nomEl.value.trim();
    pintarCabeceraCliente();
  }
  const ranges = {};
  const sinRango = [];
  for (const [k, entradas] of Object.entries(leerRangosDOM())) {
    const filas = [];
    for (const e of entradas) {
      if (e.min !== "" && e.max !== "") {
        const fila = { min: parseInt(e.min), max: parseInt(e.max) };
        if (e.prod_id) { fila.prod_id = e.prod_id; fila.prod_nombre = e.prod_nombre; }
        if (e.split === 3 || e.split === 5) { fila.split = e.split; fila.cada_min = e.cada_min; }
        filas.push(fila);
      } else if (e.prod_id) {
        // La calidad viaja dentro del rango: sin min-max no hay orden precreada
        // que la use. Ya está avisado en la fila (aviso ámbar), acá solo se cuenta.
        sinRango.push(k);
      }
    }
    if (filas.length) ranges[k] = filas;
  }
  // Comentarios por post: viaja adentro de `ranges` (es config de cantidades),
  // pero como clave propia — el backend no lo mete en el loop de productos.
  const comentarios = {};
  for (const [k, e] of Object.entries(leerComentariosDOM())) {
    if (e.min !== "" && e.max !== "") comentarios[k] = { min: parseInt(e.min), max: parseInt(e.max) };
  }
  if (Object.keys(comentarios).length) ranges.comentarios = comentarios;
  if (sinRango.length)
    toast(`Sin rango en ${[...new Set(sinRango)].join(", ")}: esa calidad no se guardó`, "bad");
  const payload = {
    // Normalizado antes de salir: un "@" o un espacio pegado de un copiar-pegar
    // rompe el match del post contra el cliente.
    ig_username: document.getElementById("client-ig").value.trim().replace(/^@/, ""),
    display_name: document.getElementById("client-name").value.trim(),
    status: document.getElementById("client-status").value,
    gender: document.getElementById("client-gender").value,
    quality: document.getElementById("client-quality").value,
    keyword_mode: document.getElementById("client-keyword-mode").checked,
    ranges,
    prompt: document.getElementById("client-prompt").value,
    // El idvendedor viaja junto al idventa: el CRM imputa la orden a ese par, y
    // mezclar la venta de uno con el vendedor de otro la rechaza o la imputa mal.
    crm_idventa: document.getElementById("client-venta").value,
    crm_idvendedor: (ventaById(document.getElementById("client-venta").value) || {}).idvendedor || "",
  };
  const btn = document.getElementById("client-save");
  const btnTxt = btn.textContent;
  btn.disabled = true;
  btn.classList.add("ax-btn--busy");
  btn.textContent = "Guardando…";
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
  } catch (e) { showErr("client-err", e.message); }
  finally { btn.disabled = false; btn.classList.remove("ax-btn--busy"); btn.textContent = btnTxt; }
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
      // Cuál de las fichas del sistema: cada una se edita con su propio
      // asistente (el genérico y el de palabra clave no son lo mismo).
      system_key: (abierto && abierto.reserved) ? abierto.ig_username : "",
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
