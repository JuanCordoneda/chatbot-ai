// Panel de admin self-serve (TAREA 3) — versión pulida. Clases con prefijo ax-.

// ── Tema ──
function toggleTheme() {
  const isLight = document.body.classList.toggle("light");
  localStorage.setItem("theme", isLight ? "light" : "dark");
  const btn = document.getElementById("btn-theme");
  if (btn) btn.textContent = isLight ? "🌙 Dark" : "☀ Light";
  // Los avatares se pintan distinto en cada tema (ver avatarStyle) y su color
  // va inline: sin redibujar, cambiar de tema dejaba las iniciales del tema
  // anterior sobre el fondo nuevo.
  if (clientsCache.length || sistemaCache.length) renderClients();
  if (usuariosCache.length) renderUsuarios();
  if (vendedoresCache.length) renderVendedores();
}
(function () {
  if (localStorage.getItem("theme") === "light") document.body.classList.add("light");
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("btn-theme");
    if (btn && document.body.classList.contains("light")) btn.textContent = "🌙 Dark";
    // El bloque del usuario solo se pinta si hay sesión con nombre. Sin la
    // guarda, el TypeError cortaba el listener acá y se llevaba puesto también
    // el orden guardado de más abajo.
    const nameEl = document.getElementById("me-name");
    const avEl = document.getElementById("me-av");
    if (nameEl && avEl) {
      const n = (nameEl.textContent || "A").trim();
      avEl.textContent = (n[0] || "A").toUpperCase();
    }
    // El orden elegido la vez pasada (el filtro lo toma renderClients solo).
    const orden = localStorage.getItem("admin_cli_orden");
    const sel = document.getElementById("cli-sort");
    if (orden && sel && [...sel.options].some(o => o.value === orden)) sel.value = orden;
  });
})();

// ── Helpers ──
// Techo de espera de cualquier llamada. Sin esto, una request colgada (VPN que
// se cae, backend trabado) dejaba el botón en "Guardando…" para siempre y sin
// una sola pista de qué pasó.
const API_TIMEOUT = 25000;

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  let timer = null;
  if (ctrl) { opts.signal = ctrl.signal; timer = setTimeout(() => ctrl.abort(), API_TIMEOUT); }
  let r;
  try {
    r = await fetch(url, opts);
  } catch (e) {
    // fetch solo rechaza por red o abort: el mensaje del navegador ("Failed to
    // fetch") no le dice nada a nadie.
    throw new Error(e && e.name === "AbortError"
      ? "El servidor tardó demasiado en responder. Probá de nuevo."
      : "Sin conexión con el servidor. Revisá la red y probá de nuevo.");
  } finally {
    if (timer) clearTimeout(timer);
  }
  // La sesión vencida devuelve el login: sin este caso, el usuario ve "Error
  // 401" y no entiende que tiene que volver a entrar.
  if (r.status === 401 || r.status === 403) {
    let d = {}; try { d = await r.json(); } catch (e) {}
    throw new Error(d.error || "Se cerró la sesión. Volvé a entrar para seguir.");
  }
  let data = {}; try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
  return data;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function hue(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
// Un tono por @usuario para reconocer la fila de un vistazo, pero apagado: a
// 62% de saturación ocho tarjetas seguidas eran ocho gradientes chillones que
// tapaban al propio nombre y peleaban con el amarillo de la marca.
function avatarStyle(seed) {
  const h = hue(seed);
  const claro = document.body.classList.contains("light");
  return claro
    // En claro el bloque saturado manchaba una lista que es casi toda blanca:
    // pastilla suave con las iniciales en el mismo tono, más oscuro.
    ? `background:hsl(${h},46%,92%);color:hsl(${h},52%,32%)`
    : `background:linear-gradient(135deg,hsl(${h},34%,44%),hsl(${(h + 30) % 360},34%,37%))`;
}
function initials(name, handle) {
  const s = (name || handle || "?").trim();
  const parts = s.split(/\s+/);
  return ((parts[0][0] || "") + (parts[1] ? parts[1][0] : "")).toUpperCase() || "?";
}

let toastTimer = null;
// `accion` (opcional) agrega un botón al toast: {label, run}. Es para las cosas
// que se hacen de un clic y se arrepienten al segundo siguiente (pausar).
function toast(msg, kind = "", accion = null) {
  const t = document.getElementById("toast");
  const ico = kind === "bad"
    ? '<svg class="ax-ti" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="8" stroke="#ff6b6b" stroke-width="1.6"/><path d="M7 7l6 6M13 7l-6 6" stroke="#ff6b6b" stroke-width="1.6" stroke-linecap="round"/></svg>'
    : kind === "ok"
    ? '<svg class="ax-ti" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="8" stroke="#4ade80" stroke-width="1.6"/><path d="M6 10l3 3 5-6" stroke="#4ade80" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    : "";
  t.className = "ax-toast ax-on" + (kind ? " ax-" + kind : "");
  t.innerHTML = ico + esc(msg);
  if (accion) {
    const b = document.createElement("button");
    b.className = "ax-toast-a";
    b.textContent = accion.label;
    b.onclick = () => { t.classList.remove("ax-on"); accion.run(); };
    t.appendChild(b);
  }
  clearTimeout(toastTimer);
  // Con acción dura más: 2,4s no alcanzan para leer y decidir.
  toastTimer = setTimeout(() => t.classList.remove("ax-on"), accion ? 6000 : 2400);
}

function switchTab(name) {
  document.querySelectorAll(".ax-seg .ax-tab").forEach(t => t.classList.toggle("ax-on", t.dataset.tab === name));
  document.querySelectorAll(".ax-panel").forEach(p => p.classList.remove("ax-on"));
  document.getElementById("panel-" + name).classList.add("ax-on");
  if (name === "uso") loadUso();
  if (name === "tokens") loadTokens();
  if (name === "usuarios") loadUsuarios();
  if (name === "pedidos") loadPedidos();
  if (name === "cola") loadCola();
  if (name === "crm") loadCrm();
  if (name === "instagram") loadIgSesiones();
}

// ── Modales ──
// Mientras haya un modal abierto la página de atrás no scrollea: con el modal
// de cliente a pantalla completa, la rueda del mouse movía la lista de abajo y
// al cerrar aparecías en otro lado.
function _sincronizarScrollLock() {
  document.body.classList.toggle("ax-locked", !!document.querySelector(".ax-mo.ax-on"));
}

// Primer control de verdad del modal, para arrancar el tab adentro y no en el
// botón de cerrar.
function _primerFoco(mo) {
  return mo.querySelector(
    'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not(.ax-seg-src):not([disabled]), .ax-opt-b[aria-checked="true"], .ax-btn--primary, button:not([disabled])'
  );
}

function openMo(id) {
  const mo = document.getElementById(id);
  if (!mo) return;
  // Quién tenía el foco antes de abrir: al cerrar se lo devolvemos, si no el
  // tab arranca de cero arriba de la página.
  mo._focoPrevio = document.activeElement;
  mo.classList.add("ax-on");
  _sincronizarScrollLock();
  // Los modales que enfocan un campo concreto lo hacen ellos (con su setTimeout);
  // el resto arranca en el primer control en vez de dejar el foco en el body.
  if (!mo.dataset.focoPropio) {
    const el = _primerFoco(mo);
    if (el) setTimeout(() => { if (mo.classList.contains("ax-on")) el.focus(); }, 50);
  }
}
function closeMo(id) {
  const mo = document.getElementById(id);
  if (!mo) return;
  mo.classList.remove("ax-on");
  _sincronizarScrollLock();
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
  // Por closeMo y no a mano: es el único que devuelve el foco y suelta el
  // scroll de la página.
  closeMo(e.target.id);
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
    [...document.querySelectorAll(".ax-mo.ax-on")].forEach(m => closeMo(m.id));
  }
  if (e.key === "Enter" && !e.shiftKey) {
    // El ÚLTIMO abierto, no el primero del DOM: con el confirm encima de la
    // ficha, `querySelector` devolvía client-mo y Enter apretaba "Guardar
    // cliente" en vez de confirmar lo que se estaba preguntando.
    const open = [...document.querySelectorAll(".ax-mo.ax-on")].pop();
    if (!open) return;
    if (document.activeElement && document.activeElement.tagName === "TEXTAREA") return;
    const save = open.querySelector("#confirm-ok, .ax-btn--primary");
    if (save && !save.disabled) { e.preventDefault(); save.click(); }
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

// Cada carga se lleva un número. Si mientras vuelve la respuesta se pidió otra
// (cambiar de vendedor dos veces seguidas, guardar y refrescar), la vieja se
// descarta: si no, la respuesta lenta del vendedor A pisaba la lista del B.
let _cargaClientes = 0;

async function loadClients() {
  const list = document.getElementById("clientes-list");
  const token = ++_cargaClientes;
  if (!selectedVendedor) {
    clientsCache = [];
    invalidarFondos();
    // Sin vendedor no hay nada que filtrar: las chips del anterior no quedan.
    document.getElementById("cli-filtros").innerHTML = "";
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
    if (token !== _cargaClientes) return;   // llegó tarde: ya hay otra carga en curso
    // El genérico viaja en la misma respuesta (solo para el admin) pero se
    // guarda aparte: no es un cliente del vendedor y no cuenta en los KPIs.
    sistemaCache = clients.filter(c => c.reserved);
    clientsCache = clients.filter(c => !c.reserved);
    invalidarFondos();
    renderClients(); renderKpis();
  } catch (e) {
    if (token !== _cargaClientes) return;
    // Dejar los esqueletos girando para siempre es peor que decir qué pasó.
    list.innerHTML = emptyState("No se pudo cargar la lista", e.message);
    toast(e.message, "bad");
  }
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
  // Otro vendedor, otra lista: un filtro heredado que no deja a nadie se lee
  // como "este vendedor no tiene clientes". (Guardar o pausar no lo resetea:
  // ahí se está trabajando sobre el filtro puesto a propósito.)
  cliFiltro = "todos";
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
// Índices de la corrida: `ventasCache` se recorría entero por cada llamada a
// ventasDe()/ventaById(), y esas dos se llaman una vez por tarjeta, una por
// chip de filtro y DOS POR COMPARACIÓN del sort. Con 200 clientes y 500
// campañas eso son cientos de miles de recorridas por tecla tipeada.
let _ventasPorIg = new Map();
let _ventasPorId = new Map();
// estadoFondos() es puro respecto de (cliente, ventas): se memoiza por cliente
// y se tira el cache cuando cambia cualquiera de los dos lados.
let _fondosMemo = new Map();

function _reindexarVentas() {
  _ventasPorIg = new Map();
  _ventasPorId = new Map();
  for (const v of ventasCache) {
    _ventasPorId.set(String(v.idventa), v);
    const ig = (v.ig_username || "").trim().toLowerCase();
    if (!_ventasPorIg.has(ig)) _ventasPorIg.set(ig, []);
    _ventasPorIg.get(ig).push(v);
  }
  invalidarFondos();
}

function invalidarFondos() { _fondosMemo = new Map(); }

async function loadVentas() {
  try {
    const r = await api("GET", "/api/ventas");
    ventasCache = r.ventas || [];
    ventasError = "";
  } catch (e) {
    ventasCache = [];
    ventasError = e.message || "No se pudieron leer las ventas del CRM";
  }
  _reindexarVentas();
  renderVentaSelect();
}

// Saldo por debajo del cual la campaña ya no alcanza para mandar tráfico: es el
// aviso que faltaba (la del .env venía descontando hasta quedar en $7).
const VENTA_SALDO_BAJO = 5;

function ventaById(id) {
  return _ventasPorId.get(String(id)) || null;
}

function saldoDe(v) { return parseFloat((v && v.disponible) || 0) || 0; }

// Campañas de un cliente: se agrupan por el PERFIL de IG que trae el CRM, no por
// el nombre — el mismo cliente figura como "Peter J Fouernier", "Peter Fournier"
// y "Peter Fouernier" según quién la cargó.
const _SIN_VENTAS = [];
function ventasDe(igUsername) {
  const ig = (igUsername || "").trim().toLowerCase();
  if (!ig) return _SIN_VENTAS;
  return _ventasPorIg.get(ig) || _SIN_VENTAS;
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
  const memo = _fondosMemo.get(c.id);
  if (memo) return memo;
  const f = _estadoFondos(c);
  _fondosMemo.set(c.id, f);
  return f;
}

function _estadoFondos(c) {
  // CRM caído: no sabemos nada de las campañas. Marcarlos a todos como "sin
  // campaña propia" era mentir — y encendía el aviso de arriba y el filtro
  // "Ojo con la plata" con la lista entera adentro.
  if (ventasError) {
    return { propias: _SIN_VENTAS, total: 0, venta: null, auto: false, sinDatos: true,
      nivel: "ok", texto: "sin datos del CRM",
      detalle: "No se pudieron leer las campañas: " + ventasError };
  }
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
  if (f.sinDatos)
    return `<span class="ax-venta ax-venta--sindatos" title="${esc(f.detalle)}">— sin datos del CRM</span>`;
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
  // loadVentas() la llama sin argumento. Tomando "" como el @usuario, las
  // campañas propias del cliente abierto se iban al grupo "otras" y la elegida
  // podía quedar fuera de la lista, borrando la selección en silencio.
  if (igUsername === undefined) igUsername = document.getElementById("client-ig")?.value || "";
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
    <div class="ax-card ax-card--go" role="button" tabindex="0"
         aria-label="Editar ${esc(c.display_name)}"
         onclick="abrirDesdeCard(event, ${c.id})" onkeydown="cardKey(event, ${c.id})">
      <div class="ax-avatar" style="${avatarStyle(c.ig_username)}">${esc(c.system_icon || "🌐")}</div>
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

// Segundo renglón: cómo está configurado, en números. Antes había que abrir la
// ficha de cada uno para saber si tenía cantidades cargadas o no.
function clientMeta(c) {
  const rg = c.ranges || {};
  const com = rg.comentarios || {};
  const chips = [];
  for (const [k, ico, txt] of [["verificados", "✅", "verif."], ["comunes", "💬", "comunes"]]) {
    const e = com[k];
    chips.push(e && e.min != null && e.max != null
      ? `<span class="ax-chip">${ico} <b>${e.min}–${e.max}</b> ${txt}</span>`
      : `<span class="ax-chip ax-chip--off" title="Sin cantidad fija: el vendedor la carga a mano">${ico} ${txt} a mano</span>`);
  }
  const conRango = RANGE_KEYS.filter(k => (rg[k] || []).length);
  chips.push(conRango.length
    ? `<span class="ax-chip" title="Tipos con cantidad automática: ${conRango.join(", ")}">📈 <b>${conRango.length}</b> tipo${conRango.length === 1 ? "" : "s"} de tráfico</span>`
    : `<span class="ax-chip ax-chip--off" title="Ningún producto con cantidad automática">📈 sin tráfico automático</span>`);
  return `<div class="ax-meta">${chips.join("")}</div>`;
}

// Resalta en el texto lo que se escribió en el buscador. Escapa primero y
// marca después: lo que se busca nunca entra como HTML.
function marcar(txt, q) {
  const s = esc(txt);
  if (!q) return s;
  const i = s.toLowerCase().indexOf(esc(q).toLowerCase());
  if (i < 0) return s;
  const n = esc(q).length;
  return `${s.slice(0, i)}<mark class="ax-mark">${s.slice(i, i + n)}</mark>${s.slice(i + n)}`;
}

function clientCard(c) {
  if (c.reserved) return genericCard(c);
  const sinPrompt = !c.keyword_mode && !(c.prompt || "").trim();
  const q = (document.getElementById("cli-search")?.value || "").trim();
  // La fila entera abre la ficha: es lo que todos intentan primero, y apuntarle
  // al botón "Editar" de 60px es trabajo de puntería.
  return `
    <div class="ax-card ax-card--go ${c.status === "paused" ? "ax-dimmed" : ""}"
         role="button" tabindex="0" aria-label="Editar ${esc(c.display_name || c.ig_username)}"
         onclick="abrirDesdeCard(event, ${c.id})" onkeydown="cardKey(event, ${c.id})">
      <div class="ax-avatar" style="${avatarStyle(c.ig_username)}">${esc(initials(c.display_name, c.ig_username))}</div>
      <div class="ax-main">
        <div class="ax-name">${marcar(c.display_name, q) || '<span class="ax-sinnombre">(sin nombre)</span>'}
          ${c.status === "active"
            ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Activo</span>'
            : '<span class="ax-pill ax-pill--paused"><span class="ax-pdot"></span>Pausado</span>'}
          ${c.quality === "pro"
            ? '<span class="ax-pill ax-pill--pro" title="Corre con el modelo de IA más potente">Pro</span>'
            : ""}
          ${c.keyword_mode
            ? '<span class="ax-pill ax-pill--kw" title="Sus comentarios son una sola palabra repetida, sacada del post: el prompt no se usa">🔑 Palabra clave</span>'
            : ""}
          ${sinPrompt
            ? '<span class="ax-pill ax-pill--falta" title="Sin instrucciones propias: genera solo con las reglas generales del sistema">Sin prompt</span>'
            : ""}
        </div>
        <div class="ax-sub"><span class="ax-handle" role="button" tabindex="0"
            title="Copiar @${esc(c.ig_username)}" aria-label="Copiar @${esc(c.ig_username)}"
            data-handle="${esc(c.ig_username)}">@${marcar(c.ig_username, q)}</span>
          ${c.keyword_mode ? "" : `<span class="ax-sep">·</span> ${(c.prompt || "").length} car. de prompt`}
          ${clientMeta(c)}</div>
      </div>
      <!-- A la derecha va solo la plata: es el único dato de la fila que se lee
           en vertical, comparando un cliente contra otro. La configuración se
           quedó en el renglón del @usuario porque crece con el contenido y así
           la fila se llena sola, en vez de dejar medio metro de vacío en el
           medio cuando el nombre es corto. -->
      <div class="ax-side-venta">${ventaBadge(c)}</div>
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
  // Que el CRM no conteste es en sí la noticia: sin esto la barra desaparecía y
  // parecía que estaba todo en orden.
  if (ventasError) {
    box.innerHTML = `⚠ <b>No se pudieron leer las campañas del CRM.</b> Los saldos de la lista no se están mostrando. <span class="ax-venta-extra">${esc(ventasError)}</span>`;
    box.classList.add("ax-on");
    return;
  }
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

// ── Filtros rápidos de la lista ──────────────────────────────────────────────
// Cada uno es una pregunta concreta sobre la lista. `test` decide quién entra;
// el contador de la chip sale de aplicarlo sobre todos los clientes.
const CLI_FILTROS = [
  { key: "todos",     label: "Todos",         test: () => true },
  { key: "activos",   label: "Activos",       test: c => c.status === "active" },
  { key: "pausados",  label: "Pausados",      test: c => c.status === "paused" },
  { key: "pro",       label: "Pro",           test: c => c.quality === "pro" },
  { key: "kw",        label: "Palabra clave", test: c => !!c.keyword_mode },
  { key: "sinprompt", label: "Sin prompt",    test: c => !c.keyword_mode && !(c.prompt || "").trim() },
  // Los que van a fallar al mandar tráfico: sin campaña propia o con la campaña
  // casi sin saldo / vencida.
  { key: "fondos",    label: "Ojo con la plata", test: c => estadoFondos(c).nivel !== "ok" },
];
// Filtro y orden sobreviven al F5: quien trabaja con "los que piden atención"
// no quiere volver a elegirlo cada vez que entra.
let cliFiltro = localStorage.getItem("admin_cli_filtro") || "todos";

function setCliFiltro(k) {
  cliFiltro = (cliFiltro === k && k !== "todos") ? "todos" : k;
  localStorage.setItem("admin_cli_filtro", cliFiltro);
  renderClients();
}

function setCliOrden() {
  localStorage.setItem("admin_cli_orden", document.getElementById("cli-sort").value);
  renderClients();
}

// Cada tecla redibujaba la lista entera (filtrar + ordenar + innerHTML de todas
// las tarjetas). Escribir "peter" son seis redibujos de los que cinco no se
// llegan a ver: se espera a que la mano pare.
let _busquedaTimer = null;
function buscarClientes() {
  const inp = document.getElementById("cli-search");
  // El botón de limpiar sí responde al toque: es feedback del propio campo.
  document.getElementById("cli-search-x")?.classList.toggle("ax-hidden", !inp.value.trim());
  clearTimeout(_busquedaTimer);
  _busquedaTimer = setTimeout(renderClients, 120);
}

function limpiarBusqueda() {
  const inp = document.getElementById("cli-search");
  inp.value = "";
  clearTimeout(_busquedaTimer);
  renderClients();
  inp.focus();
}

function renderFiltros(visibles, total) {
  const box = document.getElementById("cli-filtros");
  if (!box) return;
  box.innerHTML = CLI_FILTROS.map(f => {
    const n = f.key === "todos" ? clientsCache.length : clientsCache.filter(f.test).length;
    // Un filtro que no deja a nadie no se esconde: que esté en cero también es
    // información (0 pausados, 0 sin prompt).
    return `<button type="button" class="ax-filtro${cliFiltro === f.key ? " ax-on" : ""}${n ? "" : " ax-filtro--vacio"}"
      aria-pressed="${cliFiltro === f.key}" onclick="setCliFiltro('${f.key}')">${esc(f.label)}<span class="ax-filtro-c">${n}</span></button>`;
  }).join("") +
    `<span class="ax-filtro-res">${visibles === total ? `${total} cliente${total === 1 ? "" : "s"}` : `${visibles} de ${total}`}</span>`;
}

// Comparadores del <select> de orden. Todos caen en el nombre como desempate,
// así que dos recargas seguidas muestran siempre lo mismo.
const CLI_ORDEN = {
  nombre: (a, b) => (a.display_name || a.ig_username).localeCompare(b.display_name || b.ig_username),
  handle: (a, b) => a.ig_username.localeCompare(b.ig_username),
  prompt: (a, b) => (b.prompt || "").length - (a.prompt || "").length,
  saldo: (a, b) => estadoFondos(a).total - estadoFondos(b).total,
  // "Lo que hay que mirar": primero lo que no funciona bien (sin prompt, sin
  // plata), después lo pausado, al final lo que anda.
  atencion: (a, b) => _atencion(b) - _atencion(a),
};

function _atencion(c) {
  let p = 0;
  if (!c.keyword_mode && !(c.prompt || "").trim()) p += 4;
  if (estadoFondos(c).nivel !== "ok") p += 3;
  if (c.status === "paused") p += 1;
  return p;
}

function renderClients() {
  renderFondosAlert();
  const q = (document.getElementById("cli-search").value || "").trim().toLowerCase();
  document.getElementById("cli-search-x")?.classList.toggle("ax-hidden", !q);
  const list = document.getElementById("clientes-list");
  const filtro = (CLI_FILTROS.find(f => f.key === cliFiltro) || CLI_FILTROS[0]).test;
  const items = clientsCache.filter(c => filtro(c) &&
    (!q || c.ig_username.toLowerCase().includes(q) || (c.display_name || "").toLowerCase().includes(q)));
  const orden = CLI_ORDEN[document.getElementById("cli-sort")?.value] || CLI_ORDEN.nombre;
  items.sort((a, b) => orden(a, b) ||
    (a.display_name || a.ig_username).localeCompare(b.display_name || b.ig_username));
  renderFiltros(items.length, clientsCache.length);
  setTxt("cli-live", clientsCache.length
    ? `${items.length} de ${clientsCache.length} clientes a la vista`
    : "");
  // El genérico va siempre al final, en su propia sección y sin filtrar por el
  // buscador (es del sistema, no uno más de la lista).
  const sistema = sistemaCache.length
    ? `<div class="ax-group">🌐<span class="ax-group-t">Del sistema</span><span class="ax-group-c">${sistemaCache.length}</span><span class="ax-group-line"></span></div>${sistemaCache.map(genericCard).join("")}`
    : "";
  if (!clientsCache.length) { list.innerHTML = emptyState("Todavía no hay clientes", "Creá el primero con su @usuario y su prompt.") + sistema; return; }
  if (!items.length) {
    const porFiltro = cliFiltro !== "todos";
    list.innerHTML = emptyState(
      porFiltro ? "Ninguno entra en este filtro" : "Sin resultados",
      porFiltro ? "Tocá el filtro de nuevo o elegí «Todos» para ver la lista entera."
                : "Probá con otro nombre o @usuario.") + sistema;
    return;
  }

  // Agrupación por género del cliente: Hombres, Mujeres, Sin especificar.
  const GROUPS = [
    { key: "male", label: "Hombres" },
    { key: "female", label: "Mujeres" },
    { key: "none", label: "Mixto" },
  ];
  const inGroup = (c, key) => key === "none" ? (c.gender !== "male" && c.gender !== "female") : c.gender === key;

  // Los grupos por género solo tienen sentido con el orden alfabético: pedir
  // "los que piden atención" y recibir tres bloques por género es no recibir
  // ningún orden. Con cualquier otro criterio va una sola lista, y el
  // encabezado dice por qué está en ese orden.
  const clave = document.getElementById("cli-sort")?.value || "nombre";
  const POR_ORDEN = {
    atencion: ["⚠", "Primero los que piden atención"],
    saldo: ["💰", "De menos a más saldo"],
    prompt: ["✎", "Del prompt más largo al más corto"],
  };
  if (POR_ORDEN[clave]) {
    const [ico, txt] = POR_ORDEN[clave];
    list.innerHTML =
      `<div class="ax-group">${ico}<span class="ax-group-t">${txt}</span><span class="ax-group-c">${items.length}</span><span class="ax-group-line"></span></div>` +
      items.map(clientCard).join("") + sistema;
    return;
  }

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

// "/" enfoca el buscador y Esc lo limpia, como en cualquier lista larga. No se
// pisa con nada: solo corre si no hay un modal abierto ni se está escribiendo.
document.addEventListener("keydown", e => {
  const inp = document.getElementById("cli-search");
  if (!inp || !document.getElementById("panel-clientes").classList.contains("ax-on")) return;
  if (document.querySelector(".ax-mo.ax-on")) return;
  const escribiendo = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  if (e.key === "/" && !escribiendo) { e.preventDefault(); inp.focus(); inp.select(); return; }
  if (e.key === "Escape" && document.activeElement === inp && inp.value) {
    e.stopPropagation();
    limpiarBusqueda();
  }
});

// Por delegación y con data-*: el @usuario ya no viaja interpolado dentro de un
// atributo onclick, así que no hay forma de que un nombre raro rompa el HTML.
document.addEventListener("click", e => {
  const h = e.target.closest?.(".ax-handle[data-handle]");
  if (h) copyHandle(h.dataset.handle, e);
});
document.addEventListener("keydown", e => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const h = e.target.closest?.(".ax-handle[data-handle]");
  if (!h) return;
  e.preventDefault();
  copyHandle(h.dataset.handle, e);
});

function copyHandle(h, ev) {
  ev?.stopPropagation();   // copiar el @ no abre la ficha
  if (!navigator.clipboard) { toast("El navegador no deja copiar desde acá", "bad"); return; }
  navigator.clipboard.writeText("@" + h)
    .then(() => toast("@" + h + " copiado", "ok"))
    // Sin permiso de portapapeles el clic no hacía absolutamente nada.
    .catch(() => toast("No se pudo copiar al portapapeles", "bad"));
}

// Clic en la fila = editar, salvo que se haya tocado un botón (pausar, borrar,
// pedir ajuste) o se esté seleccionando texto para copiarlo.
function abrirDesdeCard(ev, id) {
  if (ev.target.closest(".ax-acts, .ax-handle, button, a")) return;
  if ((window.getSelection()?.toString() || "").length) return;
  openClientModal(id);
}

function cardKey(ev, id) {
  if (ev.target !== ev.currentTarget) return;   // el foco está en un botón de adentro
  if (ev.key !== "Enter" && ev.key !== " ") return;
  ev.preventDefault();
  openClientModal(id);
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
          ? `<button type="button" class="ax-range-add" onclick="agregarCalidad('${k}')">+ Agregar otra calidad</button>`
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
    } else if (tieneMin && COM_TOPE[k] && parseInt(max.value) > COM_TOPE[k]) {
      malo = `Los comunes salen en dos tandas de 40: el tope es ${COM_TOPE[k]} por día.`;
    }
    min.classList.toggle("ax-bad", !!malo);
    max.classList.toggle("ax-bad", !!malo);
    if (malo && !msg) msg = malo;
    // 0-0 no es un rango a autocompletar: es "este cliente no manda ese tipo".
    if (!malo && tieneMin)
      partes.push(parseInt(max.value) === 0
        ? `sin ${k}`
        : `${min.value}–${max.value} ${k}`);
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

// Cada apertura de ficha se lleva un número, por el mismo motivo que loadClients:
// cargarCalidades() sale a la red y, cerrando y abriendo otro cliente rápido, la
// vuelta de la primera redibujaba los rangos del cliente anterior sobre la ficha
// nueva — y encima pisaba el snapshot, dejándola en falso "sin cambios".
let _aperturaFicha = 0;

// Trae las calidades del CRM y redibuja con ellas. Se llama al abrir la ficha.
async function renderCalidades(rg, token) {
  rangosState = {};
  for (const k of RANGE_KEYS) rangosState[k] = _entradasDeRango(rg[k]);
  renderRangos();                 // primero sin calidades: los rangos ya se ven
  await cargarCalidades();
  if (token !== _aperturaFicha) return;
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
    soloPrompt(),
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

// El modo del prompt son dos radios (capas / solo). Se lee y se escribe por
// acá para no repetir en cinco lugares cuál de los dos es el que manda.
function soloPrompt() {
  return document.getElementById("client-prompt-standalone").checked;
}

function setModoPrompt(solo) {
  document.getElementById("client-prompt-standalone").checked = !!solo;
  document.getElementById("client-prompt-capas").checked = !solo;
}

// Lo de las capas ya lo dicen las dos tarjetas de "¿qué se le manda al modelo?",
// así que este pie no lo repite: se queda con lo único que no está escrito en
// ningún otro lado — que el contexto del post lo agrega la herramienta sola.
function pintarAyudaPrompt(gen) {
  const help = document.getElementById("client-prompt-help");
  if (!help) return;
  help.innerHTML = gen
    ? "Estas reglas se aplican a <b>todos los clientes</b>, arriba de las instrucciones propias de cada uno. Es el lugar para los arreglos generales (ej: que no todos los comentarios arranquen en minúscula)."
    : "Escribí solo las instrucciones de estilo. El <b>texto del post, sus comentarios y la imagen</b> se agregan solos al generar — no tenés que ponerlos vos.";
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
  // "Usar solo este prompt" no aplica en las fichas del sistema (el genérico ES
  // la capa de arriba) ni con palabra clave prendida (ahí no se usa el prompt).
  const idAbierto = document.getElementById("client-id").value;
  const gen = !!(idAbierto && (findClient(parseInt(idAbierto)) || {}).reserved);
  document.getElementById("client-solo-field").classList.toggle("ax-hidden", gen || kw);
  pintarAyudaPrompt(gen);
  // Chip en la tarjeta cerrada: que no haya que abrir el editor para saber si
  // este cliente recibe el prompt del sistema o no.
  document.getElementById("client-prompt-teaser-solo")
    .classList.toggle("ax-hidden", gen || kw || !soloPrompt());
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
  const token = ++_aperturaFicha;
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
  // Cliente nuevo nace con su prompt solo (sin la capa genérica arriba); las
  // fichas ya cargadas muestran lo que tengan guardado.
  setModoPrompt(c ? !!c.prompt_standalone : true);
  // Las fichas del sistema no son un cliente: no tienen posts propios. Y el
  // genérico ES la capa de arriba, así que "usar solo este prompt" no aplica.
  document.getElementById("client-keyword-field").classList.toggle("ax-hidden", gen);
  renderVentaSelect(c ? c.ig_username : document.getElementById("client-ig").value);
  document.getElementById("client-venta").value = c && c.crm_idventa ? c.crm_idventa : "";
  actualizarVentaHint();
  const rg = (c && c.ranges) || {};
  renderComentarios(rg.comentarios);   // antes de renderCalidades: entra en el snapshot
  renderCalidades(rg, token);   // async: dibuja las filas y las llena con lo del CRM
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
  pintarAyudaPrompt(gen);
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
  // Cambiar el modo del prompt no se ve hasta la próxima tanda de comentarios, y
  // para entonces ya nadie se acuerda de que lo tocó. Se pregunta SOLO cuando
  // cambia respecto de lo guardado (no en cada guardada, ni al crear: ahí el
  // modo es la decisión que se está tomando y ya está a la vista).
  const guardado = id ? findClient(parseInt(id)) : null;
  const kwOn = document.getElementById("client-keyword-mode").checked;
  if (guardado && !gen && !kwOn && soloPrompt() !== !!guardado.prompt_standalone) {
    const nombreCli = guardado.display_name || "@" + guardado.ig_username;
    const ok = await confirmDialog(soloPrompt() ? {
      title: "¿Sacarle el prompt del sistema?",
      text: `${nombreCli} va a generar SOLO con las instrucciones de su ficha. Las reglas `
          + "generales de la agencia dejan de aplicarse acá: el idioma, el tono y los "
          + "personajes tienen que estar escritos en su prompt. La herramienta le mantiene "
          + "el piso para que no suene a bot. Se nota recién en la próxima tanda.",
      okLabel: "Sí, usar solo su prompt",
    } : {
      title: "¿Volver a sumarle el prompt del sistema?",
      text: `${nombreCli} va a recibir las reglas generales arriba de sus instrucciones. `
          + "Si su prompt ya las repite, le van a llegar dos veces — conviene revisarlo. "
          + "Se nota recién en la próxima tanda.",
      okLabel: "Sí, sumar el del sistema",
    });
    if (!ok) return;
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
    prompt_standalone: soloPrompt(),
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

function togglePause(id) {
  const c = clientsCache.find(x => x.id === id); if (!c) return;
  setClientStatus(id, c.status === "active" ? "paused" : "active");
}

// El estado va explícito (y no "el contrario del que tenía"): el Deshacer se
// ejecuta después de recargar la lista, y ahí "el contrario" ya es otro.
async function setClientStatus(id, status, silencioso = false) {
  const c = clientsCache.find(x => x.id === id); if (!c) return;
  const previo = c.status;
  try {
    await api("PATCH", cliUrl(`/${id}`), { status });
    // Pausar es un clic en una fila de una lista larga: errarle al cliente de al
    // lado es fácil y, sin deshacer, se descubre horas después.
    if (!silencioso)
      toast(`@${c.ig_username} ${status === "paused" ? "pausado" : "activado"}`, "ok",
        { label: "Deshacer", run: () => setClientStatus(id, previo, true) });
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
    ? "Actualizá sus datos y su configuración del CRM."
    : "El vendedor entra a la plataforma con este email y su contraseña de Growi.";
  document.getElementById("ven-id").value = v ? v.id : "";
  document.getElementById("ven-name").value = v ? v.name : "";
  document.getElementById("ven-email").value = v ? (v.crm_email || "") : "";
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
  // Sin contraseña: no se guarda, así que el admin no la carga ni la cambia.
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

// ── Tokens (gasto de IA por vendedor) ────────────────────────────────────────
// "Uso" cuenta acciones; esto cuenta plata. Son dos preguntas distintas: un
// vendedor puede generar poco y gastar mucho (posts con carrusel, reintentos,
// cliente en calidad pro).

// Los montos son chicos (centavos por tanda): con 2 decimales casi todo se ve
// como $0.00 y parece que no gasta nadie.
function usd(n) {
  const v = Number(n || 0);
  if (!v) return "$0";
  return v < 1 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

function miles(n) {
  const v = Number(n || 0);
  return v >= 1e6 ? (v / 1e6).toFixed(1) + "M"
       : v >= 1e3 ? (v / 1e3).toFixed(1) + "k"
       : String(v);
}

const TOK_KINDS = { generacion: "Generación", keyword: "Keyword", descripcion: "Descripción (visión)" };
const TOK_MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

// "2026-08" -> "Ago 2026". Sin new Date(clave): parsear "2026-08" como fecha lo
// interpreta en UTC y en Argentina devolvía el mes anterior.
function mesLabel(clave, conAño = true) {
  const [a, m] = String(clave).split("-");
  const nom = TOK_MESES[Number(m) - 1] || clave;
  return conAño ? `${nom} ${a}` : nom;
}

// Último día del mes: día 0 del siguiente. Sirve para el ?hasta= del atajo.
function _finDeMes(a, m) { return new Date(a, m + 1, 0); }
function _iso(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Los últimos 6 meses como chips. El mes es la unidad con la que se factura y
// con la que se piensa el gasto, así que va primero; el rango libre es el escape.
function renderTokMeses() {
  const box = document.getElementById("tok-meses");
  if (!box) return;
  const hoy = new Date();
  const chips = [];
  for (let i = 5; i >= 0; i--) {
    const d = new Date(hoy.getFullYear(), hoy.getMonth() - i, 1);
    chips.push(`<div class="ax-tok-mes" data-desde="${_iso(d)}" data-hasta="${_iso(_finDeMes(d.getFullYear(), d.getMonth()))}"
      onclick="tokElegirMes(this)">${mesLabel(_iso(d).slice(0, 7), d.getFullYear() !== hoy.getFullYear())}</div>`);
  }
  // "Todo el período" = sin fechas: el backend cae a sus últimos 6 meses.
  chips.push(`<div class="ax-tok-mes ax-on" data-desde="" data-hasta="" onclick="tokElegirMes(this)">Últimos 6 meses</div>`);
  box.innerHTML = chips.join("");
}

function tokElegirMes(el) {
  document.getElementById("tok-desde").value = el.dataset.desde;
  document.getElementById("tok-hasta").value = el.dataset.hasta;
  loadTokens();
}

async function loadTokens() {
  const box = document.getElementById("tokens-list");
  if (!box) return;
  if (!document.getElementById("tok-meses").children.length) renderTokMeses();

  const desde = document.getElementById("tok-desde").value;
  const hasta = document.getElementById("tok-hasta").value;
  // El chip queda marcado solo si el rango coincide exacto con él: si tocaste
  // las fechas a mano, ninguno miente diciendo que estás viendo ese mes.
  document.querySelectorAll("#tok-meses .ax-tok-mes").forEach(c =>
    c.classList.toggle("ax-on", c.dataset.desde === desde && c.dataset.hasta === hasta));

  if (desde && hasta && desde > hasta) {
    toast("La fecha 'desde' es posterior a 'hasta'", "bad");
    return;
  }

  const qs = new URLSearchParams();
  if (desde) qs.set("desde", desde);
  if (hasta) qs.set("hasta", hasta);
  box.innerHTML = '<div class="ax-skeleton"></div><div class="ax-skeleton"></div>';
  try {
    const d = await api("GET", `/api/tokens?${qs}`);
    renderTokens(d);
  } catch (e) {
    box.innerHTML = emptyState("No pude traer el gasto", e.message);
    document.getElementById("tok-chart").innerHTML = "";
    toast(e.message, "bad");
  }
}

// Barras del total mes a mes. Clickear una barra filtra a ese mes.
function renderTokChart(serie) {
  const box = document.getElementById("tok-chart");
  // Con un solo mes no hay evolución que mostrar: el KPI ya dice el número.
  if (!serie || serie.length < 2) { box.innerHTML = ""; return; }
  const max = Math.max(...serie.map(m => m.costo_usd), 0.0001);
  box.innerHTML = serie.map(m => {
    const [a, mm] = m.mes.split("-");
    const fin = _iso(_finDeMes(Number(a), Number(mm) - 1));
    return `<div class="ax-tok-mb ${m.costo_usd ? "" : "ax-cero"}"
      title="${mesLabel(m.mes)}: ${usd(m.costo_usd)} · ${miles(m.llamadas)} llamadas"
      onclick="tokElegirMes({dataset:{desde:'${m.mes}-01',hasta:'${fin}'}})">
      <b>${usd(m.costo_usd)}</b>
      <i style="height:${Math.max(3, (m.costo_usd / max) * 100)}%"></i>
      <span>${mesLabel(m.mes, false)}</span>
    </div>`;
  }).join("");
}

// Lo que Anthropic facturó, al lado de lo que estimamos. La brecha es la señal:
// si se abre, la tabla de precios del código quedó vieja (o alguien más está
// usando la misma API key).
function kpiFacturado(f, estimado) {
  if (!f || !f.disponible) {
    return `<div class="ax-tok-kpi"><b>—</b><span>Facturado: falta ANTHROPIC_ADMIN_KEY</span></div>`;
  }
  if (f.error) {
    return `<div class="ax-tok-kpi ax-tok-kpi--warn"><b>—</b><span>${esc(f.error)}</span></div>`;
  }
  const real = Number(f.total_usd || 0);
  // La desviación se mide contra lo facturado (es el número verdadero), y sólo
  // se muestra si hay algo que comparar: con $0 facturado el porcentaje es
  // infinito y no dice nada.
  const desvio = real ? ((estimado - real) / real) * 100 : null;
  const alerta = desvio !== null && Math.abs(desvio) >= 10;
  const signo = desvio > 0 ? "+" : "";
  return `<div class="ax-tok-kpi ${alerta ? "ax-tok-kpi--warn" : ""}">
    <b>${usd(real)}</b>
    <span>Facturado por Anthropic${desvio !== null ? ` · estimamos ${signo}${desvio.toFixed(0)}%` : ""}</span>
  </div>`;
}

function renderTokens(d) {
  const box = document.getElementById("tokens-list");
  const kpis = document.getElementById("tok-kpis");
  const vs = d.vendedores || [];
  const total = d.total || {};
  const huerfano = d.sin_atribuir || {};

  renderTokChart(total.por_mes);

  const desperdicio = Number(total.costo_reintentos_usd || 0);
  const rango = d.desde && d.hasta ? `${d.desde} → ${d.hasta}` : "período";
  const meses = (total.por_mes || []).filter(m => m.costo_usd);
  // Con más de un mes, el promedio mensual es lo que sirve para proyectar; con
  // uno solo repetiría el total y confunde.
  const prom = meses.length > 1
    ? meses.reduce((s, m) => s + m.costo_usd, 0) / meses.length : 0;
  kpis.innerHTML = [
    `<div class="ax-tok-kpi"><b>${usd(total.costo_usd)}</b><span>Gasto estimado · ${esc(rango)}</span></div>`,
    kpiFacturado(d.facturado, total.costo_usd),
    prom ? `<div class="ax-tok-kpi"><b>${usd(prom)}</b><span>Promedio por mes (${meses.length} meses con gasto)</span></div>` : "",
    `<div class="ax-tok-kpi"><b>${miles(total.llamadas)}</b><span>Llamadas a la IA</span></div>`,
    `<div class="ax-tok-kpi"><b>${miles(total.input_tokens)} / ${miles(total.output_tokens)}</b><span>Tokens entrada / salida</span></div>`,
    // Los reintentos son generaciones descartadas: el número sube cuando la IA
    // viene cortando tandas, y ahí hay algo para revisar.
    desperdicio ? `<div class="ax-tok-kpi ax-tok-kpi--warn"><b>${usd(desperdicio)}</b><span>Gastado en reintentos</span></div>` : "",
    // Lo viejo (antes de que se guardara el vendedor) o lo que no se pudo
    // resolver. Se muestra aparte para que la suma de la tabla cierre.
    huerfano.llamadas ? `<div class="ax-tok-kpi"><b>${usd(huerfano.costo_usd)}</b><span>Sin vendedor identificado</span></div>` : "",
  ].join("");

  if (!vs.length) {
    box.innerHTML = emptyState("Sin gasto registrado",
      "Cuando el equipo genere comentarios vas a ver acá cuánto consume cada vendedor.");
    return;
  }

  const max = Math.max(...vs.map(v => v.costo_usd), 0.0001);
  box.innerHTML = vs.map((v, i) => {
    const nm = v.name || v.crm_email || "?";
    const waste = v.costo_reintentos_usd || 0;
    const kinds = Object.entries(v.por_kind || {}).sort((a, b) => b[1] - a[1]);
    // Aviso honesto: parte del número puede venir de filas viejas imputadas por
    // el @cliente, no de una atribución directa.
    const est = v.estimado
      ? `<em title="Llamadas sin vendedor guardado, imputadas por el @cliente">· ${v.estimado} estimadas</em>` : "";
    return `<div class="ax-tok-row" id="tok-row-${i}">
      <div class="ax-tok-head" onclick="document.getElementById('tok-row-${i}').classList.toggle('ax-on')">
        <div class="ax-tok-name">
          <div class="ax-avatar" style="width:28px;height:28px;border-radius:8px;font-size:.78rem;${avatarStyle(nm)}">${esc((nm[0] || "?").toUpperCase())}</div>
          ${esc(nm)} ${est}
        </div>
        <div class="ax-tok-costo">${usd(v.costo_usd)}
          <small>${miles(v.llamadas)} llamadas · ${miles(v.input_tokens)} in / ${miles(v.output_tokens)} out</small>
        </div>
      </div>
      <div class="ax-tok-bar">
        <i style="width:${(v.costo_usd / max) * 100}%"></i>
        <i class="ax-tok-waste" style="width:${(waste / max) * 100}%"></i>
      </div>
      <div class="ax-tok-det">
        <h5>Mes a mes</h5>
        <ul>${(v.por_mes || []).map(m =>
          `<li><span>${esc(mesLabel(m.mes))}</span><span>${usd(m.costo_usd)} · ${miles(m.llamadas)} llamadas</span></li>`
        ).join("") || "<li>—</li>"}</ul>
        <h5>Por usuario</h5>
        <ul>${(v.usuarios || []).map(u =>
          `<li><span>${esc(u.username)}</span><span>${usd(u.costo_usd)} · ${miles(u.llamadas)} llamadas</span></li>`
        ).join("") || "<li>Sin desglose</li>"}</ul>
        <h5>Por tipo de llamada</h5>
        <ul>${kinds.map(([k, c]) =>
          `<li><span>${esc(TOK_KINDS[k] || k)}</span><span>${usd(c)}</span></li>`
        ).join("") || "<li>—</li>"}</ul>
        <h5>Clientes que más consumen</h5>
        <ul>${(v.clientes || []).map(c =>
          `<li><span>@${esc(c.clave)}</span><span>${usd(c.costo_usd)} · ${miles(c.llamadas)} llamadas</span></li>`
        ).join("") || "<li>—</li>"}</ul>
        ${waste ? `<h5>Reintentos</h5><ul><li><span>Generaciones descartadas</span><span>${usd(waste)}</span></li></ul>` : ""}
      </div>
    </div>`;
  }).join("");
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
  // Validación acá y no en el 400: alta sin usuario o con pass corta se sabe sin
  // salir a la red, y no se pierde lo tipeado.
  if (!id) {
    if (!(payload.username || "").trim()) { showErr("usr-err", "Poné un nombre de usuario"); return; }
    if ((pass || "").length < 4) { showErr("usr-err", "La contraseña tiene que tener al menos 4 caracteres"); return; }
  } else if (pass && pass.length < 4) {
    showErr("usr-err", "La contraseña tiene que tener al menos 4 caracteres"); return;
  }
  hideErr("usr-err");
  // Enter en el modal dispara el botón primario: sin deshabilitarlo, dos Enter
  // seguidos creaban el usuario dos veces.
  const btn = document.getElementById("usr-save");
  if (btn) { if (btn.disabled) return; btn.disabled = true; }
  try {
    if (id) await api("PATCH", `/api/admin/usuarios/${id}?vendedor=${selectedVendedor}`, payload);
    else await api("POST", usrUrl(), payload);
    closeMo("usr-mo");
    toast(id ? "Usuario actualizado" : "Usuario creado", "ok");
    await loadUsuarios();
  } catch (e) { showErr("usr-err", e.message); }
  finally { if (btn) btn.disabled = false; }
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
// El monitor devuelve los tiempos como epoch en segundos (es lo que usa
// internamente); la lista, como ISO desde la base. Dos formatos, dos helpers.
function horaDeEpoch(seg) {
  if (!seg) return "nunca";
  return new Date(seg * 1000).toLocaleString("es-AR",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

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

// ── Cola de órdenes que no salieron al CRM ───────────────────────────────────
// Un envío puede fallar por red (proxy caído). En vez de perder los comentarios
// ya generados, la orden queda encolada y un worker la reintenta sola. Esta
// vista existe para que eso no sea invisible: sin ella, una orden en "revisar"
// no la mira nadie hasta que el cliente reclama.

let colaCache = [];

const COLA_ESTADOS = {
  pendiente: { txt: "En espera", cls: "ax-pill--paused" },
  enviando:  { txt: "Enviando",  cls: "ax-pill--paused" },
  enviada:   { txt: "Enviada",   cls: "ax-pill--active" },
  revisar:   { txt: "Revisar",   cls: "ax-pill--paused" },
  fallida:   { txt: "Fallida",   cls: "" },
  cancelada: { txt: "Cancelada", cls: "" },
};

async function loadCola() {
  const sel = document.getElementById("cola-estado");
  const estados = sel ? sel.value : "pendiente,enviando,revisar";
  try {
    const d = await api("GET", `/api/ordenes-pendientes${estados ? `?estados=${estados}` : ""}`);
    colaCache = d.ordenes || [];
    renderCola();
    // El contador de la pestaña cuenta lo que REQUIERE atención: lo que espera
    // salir más lo que quedó trabado. Las enviadas no suman: ya están.
    const c = d.conteo || {};
    setTxt("tab-cola-cnt", (c.pendiente || 0) + (c.enviando || 0) + (c.revisar || 0) + (c.fallida || 0));
  } catch (e) { toast(e.message, "bad"); }
}

function colaCard(o) {
  const meta = COLA_ESTADOS[o.estado] || { txt: o.estado, cls: "" };
  const cerrada = ["enviada", "cancelada"].includes(o.estado);
  const p = o.payload || {};
  const nComs = (p.comentarios || []).length;
  const nOrds = (p.ordenes || []).length;

  // "revisar" es el caso delicado: el envío pudo haber llegado al CRM. Nunca se
  // reintenta solo, y hay que decir POR QUÉ o el admin no sabe qué hacer.
  const aviso = o.estado === "revisar"
    ? `<div class="ax-ped-txt">Puede haber entrado en Growi. Revisá el CRM antes de volver a mandarla: reintentarla a ciegas duplicaría la orden.</div>`
    : o.estado === "fallida"
    ? `<div class="ax-ped-txt">Se agotaron los reintentos. Hay que cargarla a mano o volver a generarla.</div>`
    : "";
  const err = o.ultimo_error && !cerrada
    ? `<div class="ax-sub" style="opacity:.8;">Último error: ${esc(o.ultimo_error)}</div>` : "";
  const reintento = o.estado === "pendiente" && o.proximo_intento
    ? ` · próximo intento ${fechaCorta(o.proximo_intento)}` : "";

  return `
    <div class="ax-card ${cerrada ? "ax-dimmed" : ""}" style="align-items:flex-start;">
      <div class="ax-avatar" style="${avatarStyle(o.client_ig_username || "?")}">${esc(initials("", o.client_ig_username || "?"))}</div>
      <div class="ax-main">
        <div class="ax-name">
          ${o.client_ig_username ? "@" + esc(o.client_ig_username) : "Sin cliente"}
          <span class="ax-pill ${meta.cls}"><span class="ax-pdot"></span>${meta.txt}</span>
        </div>
        <div class="ax-sub">
          ${nOrds} ${nOrds === 1 ? "orden" : "órdenes"} · ${nComs} comentarios ·
          ${o.intentos} ${o.intentos === 1 ? "intento" : "intentos"} · ${fechaCorta(o.created_at)}${reintento}
        </div>
        <div class="ax-sub"><a href="${esc(o.post_url)}" target="_blank" rel="noopener">Ver el post</a></div>
        ${aviso}${err}
      </div>
      <div class="ax-acts">
        ${cerrada ? "" : `<button class="ax-btn ax-btn--sm" onclick="cancelarOrdenCola(${o.id})">Dar de baja</button>`}
      </div>
    </div>`;
}

function renderCola() {
  const list = document.getElementById("cola-list");
  if (!list) return;
  if (!colaCache.length) {
    list.innerHTML = emptyState("No hay órdenes en cola",
      "Cuando un envío al CRM falle por conexión, la orden queda acá y se reintenta sola.");
    return;
  }
  list.innerHTML = colaCache.map(colaCard).join("");
}

async function cancelarOrdenCola(id) {
  if (!confirm("¿Dar de baja esta orden? No se va a enviar al CRM.")) return;
  try {
    await api("POST", `/api/ordenes-pendientes/${id}/cancelar`);
    toast("Orden dada de baja", "ok");
    loadCola();
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
      // Con "usar solo este prompt" prendido no hay capa de arriba: el asistente
      // no tiene que sacar lo que "ya viene del genérico", porque no viene.
      // Se manda el estado del checkbox, no el guardado: la propuesta se pide
      // sobre la ficha como está en pantalla.
      standalone: soloPrompt(),
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
  // En ambos modos: el badge de "En cola" tiene que estar cargado de entrada,
  // sin que haya que abrir la pestaña. Es justamente lo que nadie va a mirar si
  // no lo ve solo.
  loadCola();
  // Igual que la cola: el contador de errores del CRM se carga de entrada. Un
  // envío rechazado que nadie mira es exactamente el problema que esto resuelve.
  loadCrm();
})();


// ── Trazabilidad de los envíos de órdenes al CRM ─────────────────────────────
// Growi es un CRM de terceros: no tiene panel que nos sirva ni forma de auditar
// lo que le mandamos. Cuando un vendedor dice "mandé la orden y no entró", esta
// vista es lo único capaz de contestar qué salió, qué contestaron y cuándo.
// Se registra SOLO el envío de órdenes: es lo único que mueve plata.

let crmCache = [];

const CRM_ORIGENES = {
  web: "Panel",
  openai: "Publicar",
  cola: "Reintento automático",
  whatsapp: "Bot de WhatsApp",
  monitor: "Monitor",
};

async function loadCrm() {
  const qs = new URLSearchParams();
  const q = (document.getElementById("crm-q") || {}).value || "";
  const err = (document.getElementById("crm-estado") || {}).value || "";
  if (q) qs.set("q", q);
  if (err) qs.set("errores", "1");
  try {
    const d = await api("GET", `/api/growi-calls?${qs}`);
    crmCache = d.calls || [];
    renderCrm();
    // El contador muestra ERRORES de las últimas 24h, no el total: un número de
    // llamadas no dice nada, uno de fallas sí.
    setTxt("tab-crm-cnt", (d.resumen || {}).errores || 0);
  } catch (e) { toast(e.message, "bad"); }
}

// Por qué falló, en la tarjeta. Cuando el CRM rechaza con HTTP 200 no hay
// `error` y el motivo vive en el cuerpo, así que la tarjeta decía "HTTP 200" y
// nada más: había que abrir el detalle de cada fila para enterarse de algo.
function motivoCrm(c) {
  if (c.error) return c.error;
  const cuerpo = c.response_snippet || "";
  if (!cuerpo) return "";
  try { return (JSON.parse(cuerpo).errors || []).join("; "); } catch (e) {}
  // El snippet son 300 caracteres y el mensaje del CRM es más largo, así que
  // lo normal es que el JSON venga cortado y no parsee. Se rescata igual.
  const m = cuerpo.match(/"errors"\s*:\s*\[\s*"(.+?)("|$)/);
  if (!m) return "";
  return m[1].replace(/\\u([0-9a-f]{4})/gi, (_, h) => String.fromCharCode(parseInt(h, 16)));
}

function crmCard(c) {
  const origen = CRM_ORIGENES[c.origen] || c.origen;
  const pill = c.ok
    ? `<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>OK</span>`
    : `<span class="ax-pill"><span class="ax-pdot"></span>${esc(c.status_code ? "HTTP " + c.status_code : "Falló")}</span>`;
  const quien = c.username ? esc(c.username) : "—";
  const detalle = [
    origen,
    c.duracion_ms != null ? `${c.duracion_ms} ms` : null,
    c.intentos > 1 ? `${c.intentos} intentos` : null,
    c.idventa ? `campaña ${esc(c.idventa)}` : null,
    c.costo ? `$${Number(c.costo).toFixed(2)}` : null,
  ].filter(Boolean).join(" · ");
  const motivo = motivoCrm(c);
  const err = motivo ? `<div class="ax-sub" style="opacity:.85;">${esc(motivo)}</div>` : "";

  return `
    <div class="ax-card" style="align-items:flex-start;">
      <div class="ax-avatar" style="${avatarStyle(c.client_ig_username || "envio")}">${esc(initials("", c.client_ig_username || "envío"))}</div>
      <div class="ax-main">
        <div class="ax-name">${c.client_ig_username ? "@" + esc(c.client_ig_username) : "Sin cliente"} ${pill}</div>
        <div class="ax-sub">${quien} · ${fechaCorta(c.created_at)}</div>
        <div class="ax-sub">${esc(detalle)}</div>
        ${err}
      </div>
      <div class="ax-acts">
        <button class="ax-btn ax-btn--sm" onclick="verLlamadaCrm(${c.id})">Ver detalle</button>
      </div>
    </div>`;
}

function renderCrm() {
  const list = document.getElementById("crm-list");
  if (!list) return;
  if (!crmCache.length) {
    list.innerHTML = emptyState("Sin envíos registrados",
      "Acá queda cada envío de órdenes al CRM con su respuesta: sirve para reconstruir una orden días después.");
    return;
  }
  list.innerHTML = crmCache.map(crmCard).join("");
}

// Headers a texto plano, uno por línea, como se ven en cualquier volcado HTTP.
// Devuelve null (y no "") cuando no hay: así la línea desaparece en vez de
// dejar un hueco.
function headersTxt(h) {
  if (!h || !Object.keys(h).length) return null;
  return Object.keys(h).sort().map(k => `${k}: ${h[k]}`).join("\n");
}

async function verLlamadaCrm(id) {
  const body = document.getElementById("crm-mo-body");
  const sub = document.getElementById("crm-mo-sub");
  body.innerHTML = `<div class="ax-sub">Cargando…</div>`;
  sub.textContent = "";
  openMo("crm-mo");
  try {
    const { call: c } = await api("GET", `/api/growi-calls/${id}`);
    sub.textContent = `${c.method} ${c.url}`;
    // El request tal como salió: línea de pedido, headers y cuerpo. Se arma con
    // la misma forma que un volcado HTTP para que se pueda leer (o pegar en un
    // ticket) sin traducir nada.
    const req = [
      `${c.method} ${c.url}`,
      headersTxt(c.request_headers),
      "",
      c.request_payload ? JSON.stringify(c.request_payload, null, 2) : "(sin cuerpo)",
    ].filter(x => x !== null).join("\n");

    // Y el response igual. Llega como texto porque el CRM a veces devuelve el
    // HTML del login en vez de JSON; si es JSON se muestra formateado.
    let cuerpo = c.response_body || "(sin cuerpo)";
    try { cuerpo = JSON.stringify(JSON.parse(cuerpo), null, 2); } catch (e) {}
    const resp = [
      c.status_code == null ? "(sin respuesta: la request no llegó a completarse)" : `HTTP ${c.status_code}`,
      headersTxt(c.response_headers),
      "",
      cuerpo,
    ].filter(x => x !== null).join("\n");
    const filas = [
      ["Estado", c.ok ? "OK" : "Con error"],
      ["HTTP", c.status_code == null ? "—" : c.status_code],
      ["Cuándo", fechaCorta(c.created_at)],
      ["Duración", c.duracion_ms == null ? "—" : c.duracion_ms + " ms"],
      ["Intentos", c.intentos],
      ["Origen", CRM_ORIGENES[c.origen] || c.origen],
      ["Vendedor", c.username || "—"],
      ["Cliente", c.client_ig_username ? "@" + c.client_ig_username : "—"],
      ["Costo", c.costo == null ? "—" : "$" + Number(c.costo).toFixed(2)],
      ["Campaña", c.idventa || "—"],
      ["Proxy", c.proxy || "directo"],
      ["Post", c.post_url || "—"],
      ["Trace", c.trace_id || "—"],
    ];
    body.innerHTML = `
      <div class="ax-sub" style="margin-bottom:10px;">
        ${filas.map(([k, v]) => `<div><b>${esc(k)}:</b> ${esc(v)}</div>`).join("")}
      </div>
      ${c.error ? `<div class="ax-field"><label>Error</label><div class="ax-ped-txt">${esc(c.error)}</div></div>` : ""}
      <div class="ax-field"><label>Request</label>
        <div class="ax-ped-txt" style="max-height:260px;overflow:auto;font-family:ui-monospace,monospace;font-size:.78rem;">${esc(req)}</div></div>
      <div class="ax-field"><label>Response</label>
        <div class="ax-ped-txt" style="max-height:260px;overflow:auto;font-family:ui-monospace,monospace;font-size:.78rem;">${esc(resp)}</div></div>`;
  } catch (e) {
    body.innerHTML = `<div class="ax-sub">${esc(e.message)}</div>`;
  }
}


// ── Sesiones de Instagram del scraper ────────────────────────────────────────
//
// Renovar la sesión era un trámite de escritorio: una Mac concreta con la
// cuenta abierta, permiso de Acceso Total al Disco y el CLI de Railway. Un
// sábado, con Instagram pidiendo verificación, eso fueron horas de posts sin
// imagen ni transcripción. Esta pestaña es el mismo trabajo desde el celular.

let igCache = [];

function fechaCorta(iso) {
  if (!iso) return "nunca";
  const d = new Date(iso);
  if (isNaN(d)) return "nunca";
  return d.toLocaleString("es-AR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

async function loadIgSesiones() {
  const list = document.getElementById("ig-list");
  if (!list) return;
  try {
    const d = await api("GET", "/api/admin/ig-sesiones");
    igCache = d.sesiones || [];
    pintarIgEstado(d.estado || {});
    renderIgSesiones();
  } catch (e) {
    list.innerHTML = emptyState("No pude leer las sesiones", e.message);
  }
}

function pintarIgEstado(e) {
  const caja = document.getElementById("ig-estado");
  const t = document.getElementById("ig-title");
  const sub = document.getElementById("ig-sub");
  if (!caja) return;
  caja.classList.remove("ax-ig-ok", "ax-ig-mal");
  if (e.ok === true) {
    caja.classList.add("ax-ig-ok");
    t.textContent = "La sesión de Instagram anda";
    sub.textContent = `Scrapeando con ${e.cuenta || "la cuenta cargada"}`
      + ` · último dato: ${horaDeEpoch(e.ultimo_chequeo)} (${e.origen_dato || "chequeo"})`;
  } else if (e.ok === false) {
    caja.classList.add("ax-ig-mal");
    t.textContent = "Instagram nos está rechazando";
    // El detalle importa: un checkpoint NO se arregla cargando otra cookie de
    // la misma cuenta, hay que verificarla en el navegador primero.
    sub.textContent = `${e.detalle || "sin detalle"}${
      e.caido_hace_min ? ` · caída hace ${e.caido_hace_min} min` : ""}`;
  } else {
    t.textContent = "Todavía sin datos";
    sub.textContent = "El servicio arrancó recién o está fuera del horario de chequeo.";
  }
}

function renderIgSesiones() {
  const list = document.getElementById("ig-list");
  const cnt = document.getElementById("tab-ig-cnt");
  if (cnt) cnt.textContent = igCache.length;
  if (!igCache.length) {
    list.innerHTML = emptyState(
      "Sin cuentas cargadas",
      "Está usando la sesión de la variable de entorno. Cargá al menos dos cuentas: la segunda es la que evita el corte.");
    return;
  }
  // "En uso" no es la primera de la lista: es la primera que está activa Y
  // viva. Marcar la de arriba sin mirar eso decía que estaba scrapeando con una
  // cuenta que Instagram había rechazado.
  const enUso = igCache.findIndex(x => x.activa && x.estado !== "caida");
  list.innerHTML = igCache.map((x, i) => {
    const viva = x.estado !== "caida";
    const nombre = x.username ? "@" + x.username : "cuenta " + (x.ds_user_id || x.id);
    return `
    <div class="ax-card ${x.activa ? "" : "ax-dimmed"}" style="align-items:flex-start;">
      <div class="ax-avatar" style="${avatarStyle(nombre)}">${esc(nombre[1] ? nombre[1].toUpperCase() : "?")}</div>
      <div class="ax-main">
        <div class="ax-name">${esc(nombre)}
          ${viva ? '<span class="ax-pill ax-pill--active"><span class="ax-pdot"></span>Viva</span>'
                 : '<span class="ax-pill ax-pill--off"><span class="ax-pdot"></span>Rechazada</span>'}
          ${i === enUso ? '<span class="ax-pill ax-pill--paused">en uso</span>' : ""}
          ${x.activa ? "" : '<span class="ax-pill ax-pill--off">pausada</span>'}
        </div>
        <div class="ax-sub">Última vez que anduvo: ${esc(fechaCorta(x.ultimo_ok_at))}
          · la cargó ${esc(x.creada_por || "—")}
          · <span class="ax-ig-cook">${esc(x.sessionid_masc || "")}</span></div>
        ${x.ultimo_error ? `<div class="ax-ig-err">${esc(x.ultimo_error)}</div>` : ""}
      </div>
      <div class="ax-acts">
        <button class="ax-btn ax-btn--sm" onclick="openIgModal('${esc(x.username || "")}')">Renovar</button>
        <button class="ax-btn ax-btn--sm" onclick="toggleIgSesion(${x.id}, ${x.activa ? "false" : "true"})">${x.activa ? "Pausar" : "Activar"}</button>
        ${i > 0 ? `<button class="ax-btn ax-btn--sm" onclick="subirIgSesion(${x.id})">Subir</button>` : ""}
        <button class="ax-btn ax-btn--sm" onclick="borrarIgSesion(${x.id}, '${esc(nombre)}')">Borrar</button>
      </div>
    </div>`;
  }).join("");
}

function openIgModal(username) {
  hideErr("ig-err");
  document.getElementById("ig-username").value = username || "";
  document.getElementById("ig-sessionid").value = "";
  document.getElementById("ig-otras").value = "";
  document.getElementById("ig-title-mo").textContent = username ? "Renovar @" + username : "Agregar una cuenta";
  openMo("ig-mo");
}

// Acepta lo que salga del inspector sin pedir un formato: "clave: valor",
// "clave=valor", o el JSON entero pegado de una. Pedir prolijidad en el momento
// en que algo está caído es pedir un error de tipeo.
function parsearCookies(texto) {
  const out = {};
  const t = (texto || "").trim();
  if (!t) return out;
  if (t.startsWith("{")) {
    try {
      const j = JSON.parse(t);
      for (const k of Object.keys(j)) if (j[k]) out[k] = String(j[k]);
      return out;
    } catch (e) { /* no era JSON: sigue por líneas */ }
  }
  for (const linea of t.split(/\n/)) {
    const m = linea.match(/^\s*"?([A-Za-z_][A-Za-z0-9_]*)"?\s*[:=]\s*"?([^"]*?)"?\s*,?\s*$/);
    if (m && m[2]) out[m[1]] = m[2];
  }
  return out;
}

async function saveIgSesion() {
  const btn = document.getElementById("ig-save");
  const sessionid = document.getElementById("ig-sessionid").value.trim().replace(/^["']|["']$/g, "");
  if (!sessionid) { showErr("ig-err", "Falta el sessionid: sin eso no hay sesión."); return; }
  const cookies = parsearCookies(document.getElementById("ig-otras").value);
  cookies.sessionid = sessionid;
  if (!cookies.ds_user_id) {
    // El sessionid arranca con el id de la cuenta: se saca de ahí en vez de
    // hacer que lo copien dos veces.
    const id = sessionid.split("%3A")[0].split(":")[0];
    if (/^\d+$/.test(id)) cookies.ds_user_id = id;
  }
  hideErr("ig-err");
  const antes = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Probándola contra Instagram…";
  try {
    await api("POST", "/api/admin/ig-sesiones", {
      cookies, username: document.getElementById("ig-username").value.trim(),
    });
    closeMo("ig-mo");
    toast("Sesión cargada y funcionando", "ok");
    loadIgSesiones();
  } catch (e) {
    showErr("ig-err", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = antes;
  }
}

async function probarIg() {
  const btn = document.getElementById("ig-probar");
  const antes = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Probando…";
  try {
    pintarIgEstado(await api("POST", "/api/admin/ig-sesiones/probar", {}));
    renderIgSesiones();
    loadIgSesiones();
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = antes;
  }
}

async function toggleIgSesion(id, activa) {
  try {
    await api("PATCH", "/api/admin/ig-sesiones/" + id, { activa });
    loadIgSesiones();
  } catch (e) { toast(e.message, "bad"); }
}

// "Subir" = pasar al frente de la cola. Se le da una prioridad menor que la
// primera en vez de reordenar todo: alcanza para elegir con cuál scrapear.
async function subirIgSesion(id) {
  const primera = igCache[0];
  try {
    await api("PATCH", "/api/admin/ig-sesiones/" + id,
              { prioridad: (primera ? primera.prioridad : 0) - 1 });
    loadIgSesiones();
  } catch (e) { toast(e.message, "bad"); }
}

async function borrarIgSesion(id, nombre) {
  const ok = await confirmDialog({
    title: "Borrar sesión",
    text: `Se elimina la sesión de ${nombre}. Si era la que estaba en uso, el scraper pasa a la siguiente.`,
  });
  if (!ok) return;
  try {
    await api("DELETE", "/api/admin/ig-sesiones/" + id);
    toast("Sesión borrada", "ok");
    loadIgSesiones();
  } catch (e) { toast(e.message, "bad"); }
}
