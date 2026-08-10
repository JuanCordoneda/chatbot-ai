/**
 * El bug de Pedro, clavado para que no vuelva.
 *
 * El panel manda la orden en DOS pedidos: el tráfico a /api/enviar_trafico y
 * los comentarios/reposts a /api/publicar. El de tráfico mandaba `client` e
 * `idventa`; el de comentarios NO. Sin eso el backend no puede resolver de qué
 * campaña sale la orden y la rebota con "No pudimos determinar la campaña".
 *
 * Resultado: entraban los likes y no entraban los comentarios ni los reposts,
 * con cualquier campaña, tuviera saldo o no.
 *
 * Este test corre las funciones REALES de app.js con el fetch interceptado y
 * afirma que TODO pedido que manda órdenes lleva el contexto de campaña. Si
 * alguien agrega un tercer envío y se olvida, esto se pone en rojo.
 *
 * Correr:  node scripts/tests/test_envio_lleva_campania.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.join(__dirname, "..", "..", "webService", "static", "app.js");

// Rutas que mandan órdenes al CRM: todas necesitan el contexto de campaña.
const RUTAS_DE_ORDENES = ["/api/publicar", "/api/enviar_trafico"];

const fallos = [];
function chequear(caso, cond, detalle = "") {
  console.log((cond ? "  OK   " : "  FALLA") + ` ${caso}` + (detalle ? ` — ${detalle}` : ""));
  if (!cond) fallos.push(caso);
}

// ── Un DOM de mentira, lo mínimo para que app.js cargue ──────────────────────
const nodoFalso = () => ({
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, removeEventListener() {}, appendChild() {},
  querySelector: () => nodoFalso(), querySelectorAll: () => [],
  scrollIntoView() {}, focus() {}, click() {}, insertAdjacentHTML() {},
  setAttribute() {}, getAttribute: () => null, remove() {},
  style: {}, dataset: {}, value: "", textContent: "", innerHTML: "",
  disabled: false, checked: false, files: [], options: [],
});

const documentFalso = {
  getElementById: () => nodoFalso(),
  querySelector: () => nodoFalso(),
  querySelectorAll: () => [],
  createElement: () => nodoFalso(),
  addEventListener() {}, removeEventListener() {},
  body: nodoFalso(), documentElement: nodoFalso(),
  cookie: "", readyState: "complete",
};

// ── Lo que se pidió, para revisarlo después ──────────────────────────────────
const pedidos = [];

const sandbox = {
  document: documentFalso,
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: "test" },
  location: { href: "http://test/", search: "", pathname: "/" },
  history: { replaceState() {}, pushState() {} },
  URLSearchParams,
  AbortController,
  scrollTo() {},
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  requestAnimationFrame: (f) => setTimeout(f, 0),
  alert() {}, confirm: () => true, prompt: () => null,
  Image: function () { return nodoFalso(); },
  fetch: (url, opts = {}) => {
    pedidos.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
    // Respuestas mínimas para que el flujo siga su curso.
    let data = {};
    if (url.startsWith("/api/publicar")) {
      data = { informe: "", resultado: { ok: true, insertadas: 1, errors: [] } };
    } else if (url.startsWith("/api/enviar_trafico")) {
      data = { success: true, insertadas: 1, messages: [] };
    } else if (url.startsWith("/api/server_time_ar")) {
      data = { ymdhmAR: "2026-08-10 11:32" };
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const codigo = fs.readFileSync(APP_JS, "utf8");
vm.createContext(sandbox);
try {
  vm.runInContext(codigo, sandbox, { filename: "app.js" });
} catch (e) {
  console.error("No se pudo cargar app.js en el sandbox:", e.message);
  process.exit(1);
}

// El estado de app.js vive en `let` de nivel de módulo, y esos NO son
// propiedades del sandbox: asignarlos desde afuera no toca la variable real.
// Hay que correr la asignación adentro del contexto.
const enElContexto = (js) => vm.runInContext(js, sandbox);

const POST = "https://www.instagram.com/p/Db3LyD1CWCx/";

// ── El escenario exacto de Pedro ─────────────────────────────────────────────
// Post SIN cliente asignado (por eso eligió la campaña a mano) + una orden de
// comentarios y una de tráfico, que es lo que se manda en una campaña normal.
enElContexto(`
  currentUrl = ${JSON.stringify(POST)};
  comentariosParaPublicar = ["mujeres:", "que lindo", "hombres:", "grande"];
  ordenes = [];
`);
sandbox.window._clientIg = "";
sandbox.window._clienteAsignado = false;
sandbox.window._ventaElegida = "33113";      // la que Pedro eligió a mano

const ordenComentarios = {
  tipo: "comentarios", productoNombre: "Comentarios verificados",
  comentarios: ["que lindo", "grande"], cantidad: 2, cuando: "ahora", costo: 0,
};
const ordenTrafico = {
  tipo: "trafico", redsocialId: 1, redsocial: "Instagram",
  productoNombre: "Likes 1700 JAP", link: sandbox.currentUrl,
  cantidad: 400, cuando: "ahora", costo: 0.055,
};

(async () => {
  await sandbox._ejecutarEnvio([ordenComentarios], [ordenTrafico]);

  const deOrdenes = pedidos.filter(p => RUTAS_DE_ORDENES.some(r => p.url.startsWith(r)));

  chequear("se mandan los dos pedidos de órdenes (comentarios y tráfico)",
    deOrdenes.length === 2, deOrdenes.map(p => p.url).join(" + "));

  for (const p of deOrdenes) {
    const b = p.body || {};
    chequear(`${p.url} lleva la campaña elegida`,
      b.idventa === "33113", `idventa = ${JSON.stringify(b.idventa)}`);
    chequear(`${p.url} lleva el cliente`,
      b.client !== undefined, `client = ${JSON.stringify(b.client)}`);
    chequear(`${p.url} lleva el post`,
      b.url === POST, `url = ${JSON.stringify(b.url)}`);
  }

  // Con cliente asignado la campaña la resuelve el backend, pero el cliente
  // tiene que viajar igual: es lo que ata el consumo a ese cliente.
  pedidos.length = 0;
  sandbox.window._clientIg = "cliente.test";
  sandbox.window._clienteAsignado = true;
  sandbox.window._ventaElegida = "";
  await sandbox._ejecutarEnvio([ordenComentarios], [ordenTrafico]);

  for (const p of pedidos.filter(p => RUTAS_DE_ORDENES.some(r => p.url.startsWith(r)))) {
    chequear(`${p.url} lleva el cliente asignado`,
      (p.body || {}).client === "cliente.test",
      `client = ${JSON.stringify((p.body || {}).client)}`);
  }

  console.log();
  if (fallos.length) {
    console.log(`FALLARON ${fallos.length}: ${fallos.join(" | ")}`);
    process.exit(1);
  }
  console.log("TODO OK — los dos envíos llevan campaña, cliente y post");
})();
