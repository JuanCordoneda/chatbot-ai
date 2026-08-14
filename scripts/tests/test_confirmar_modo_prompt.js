/**
 * Cambiar "¿qué se le manda al modelo?" se pregunta antes de guardar.
 *
 * El modo del prompt (las dos capas, o solo el del cliente) no se ve en ningún
 * lado hasta la próxima tanda de comentarios: si se toca sin querer — un click
 * de más en la tarjeta equivocada — el cliente empieza a generar distinto y
 * nadie lo relaciona con la ficha que abrió tres días antes. Por eso se
 * pregunta, y solo cuando CAMBIA respecto de lo que está guardado.
 *
 * Lo que se afirma acá:
 *   - cambiar el modo pregunta antes de mandar el PATCH;
 *   - decir que no CANCELA el guardado entero (no sale ningún pedido);
 *   - decir que sí guarda, y el modo nuevo viaja en el payload;
 *   - no tocar el modo NO pregunta (guardar la ficha por cualquier otra cosa
 *     no puede convertirse en un cuestionario);
 *   - con palabra clave prendida tampoco pregunta: ahí el prompt no se usa;
 *   - dar de alta un cliente tampoco: el modo es la decisión que se está
 *     tomando y está a la vista.
 *
 * Corre la saveClient() REAL de admin.js con el DOM y la red de mentira.
 *
 * Correr:  node scripts/tests/test_confirmar_modo_prompt.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ADMIN_JS = path.join(__dirname, "..", "..", "webService", "static", "admin.js");

const fallos = [];
function chequear(caso, cond, detalle = "") {
  console.log((cond ? "  OK   " : "  FALLA") + ` ${caso}` + (detalle ? ` — ${detalle}` : ""));
  if (!cond) fallos.push(caso);
}

// ── DOM de mentira ───────────────────────────────────────────────────────────
// Los nodos se cachean por id: el test necesita escribir un value o un checked
// y que saveClient lea ESE nodo, no uno nuevo cada vez.
const nodos = {};
const nuevoNodo = () => ({
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, removeEventListener() {}, appendChild() {},
  querySelector: () => nuevoNodo(), querySelectorAll: () => [],
  closest: () => null, scrollIntoView() {}, focus() {}, click() {},
  insertAdjacentHTML() {}, setAttribute() {}, getAttribute: () => null, remove() {},
  style: {}, dataset: {}, value: "", textContent: "", innerHTML: "",
  disabled: false, checked: false, files: [], options: [],
});
const porId = (id) => (nodos[id] = nodos[id] || nuevoNodo());

const documentFalso = {
  getElementById: porId,
  querySelector: () => nuevoNodo(),
  querySelectorAll: () => [],
  createElement: () => nuevoNodo(),
  addEventListener() {}, removeEventListener() {},
  body: nuevoNodo(), documentElement: nuevoNodo(),
  cookie: "", readyState: "complete",
};

const pedidos = [];
const sandbox = {
  document: documentFalso,
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: "test" },
  location: { href: "http://test/admin", search: "", pathname: "/admin" },
  history: { replaceState() {}, pushState() {} },
  URLSearchParams, AbortController,
  scrollTo() {}, addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  requestAnimationFrame: (f) => setTimeout(f, 0),
  alert() {}, confirm: () => true, prompt: () => null,
  Image: function () { return nuevoNodo(); },
  IS_ADMIN: true,
  fetch: (url, opts = {}) => {
    pedidos.push({ url, metodo: (opts.method || "GET"),
                   body: opts.body ? JSON.parse(opts.body) : null });
    return Promise.resolve({ ok: true, status: 200,
                             json: () => Promise.resolve({ client: {}, clients: [] }) });
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
try {
  vm.runInContext(fs.readFileSync(ADMIN_JS, "utf8"), sandbox, { filename: "admin.js" });
} catch (e) {
  console.error("No se pudo cargar admin.js en el sandbox:", e.message);
  process.exit(1);
}
const enElContexto = (js) => vm.runInContext(js, sandbox);

// ── Lo que no se está probando acá, de mentira ───────────────────────────────
// Validaciones de @usuario/rangos/comentarios: tienen su propio camino y sin
// esto saveClient se corta antes de llegar al confirm.
sandbox.validarIg = () => "";
sandbox.revisarRangos = () => true;
sandbox.revisarComentarios = () => true;
sandbox.leerRangosDOM = () => ({});
sandbox.leerComentariosDOM = () => ({});
sandbox.pintarCabeceraCliente = () => {};
sandbox.renderVentaSelect = () => {};
sandbox.actualizarVentaHint = () => {};
sandbox.loadClients = () => {};
sandbox.toast = () => {};
sandbox.closeMo = () => {};
sandbox.ventaById = () => ({ idvendedor: "12" });

// Los pedidos al backend, interceptados: lo que importa es SI sale y con qué.
sandbox.api = (metodo, url, payload) => {
  pedidos.push({ url, metodo, body: payload });
  return Promise.resolve({ client: {} });
};

// El confirm, interceptado: guarda lo que se preguntó y contesta lo que diga
// el caso de prueba.
let preguntas = [];
let respuesta = true;
sandbox.confirmDialog = (opts) => { preguntas.push(opts); return Promise.resolve(respuesta); };

const CLIENTE = {
  id: 7, account_id: 1, ig_username: "clientedeprueba", display_name: "Cliente de Prueba",
  status: "active", prompt: "Comentarios secos, sin emojis.", keyword_mode: false,
  prompt_standalone: false, ranges: {}, crm_idventa: "", gender: null, quality: "standard",
};

function escenario({ guardadoSolo, eligeSolo, keyword = false, nuevo = false }) {
  preguntas = [];
  pedidos.length = 0;
  enElContexto(`clientsCache = ${JSON.stringify([{ ...CLIENTE, prompt_standalone: guardadoSolo }])};
                sistemaCache = []; pedidoEnCurso = null;`);
  porId("client-id").value = nuevo ? "" : String(CLIENTE.id);
  porId("client-ig").value = CLIENTE.ig_username;
  porId("client-name").value = CLIENTE.display_name;
  porId("client-status").value = "active";
  porId("client-gender").value = "";
  porId("client-quality").value = "standard";
  porId("client-venta").value = "";
  porId("client-prompt").value = CLIENTE.prompt;
  porId("client-keyword-mode").checked = keyword;
  porId("client-prompt-standalone").checked = eligeSolo;
  porId("client-prompt-capas").checked = !eligeSolo;
  return sandbox.saveClient();
}

const patchs = () => pedidos.filter(p => p.metodo === "PATCH" || p.metodo === "POST");

(async () => {
  // ── 1. Cambia el modo y se cancela ─────────────────────────────────────────
  respuesta = false;
  await escenario({ guardadoSolo: false, eligeSolo: true });
  chequear("cambiar a 'solo este prompt' pregunta", preguntas.length === 1,
    `preguntas = ${preguntas.length}`);
  chequear("la pregunta nombra al cliente",
    (preguntas[0] || {}).text?.includes("Cliente de Prueba"), (preguntas[0] || {}).text);
  chequear("decir que no cancela el guardado", patchs().length === 0,
    `pedidos = ${JSON.stringify(patchs().map(p => p.metodo + " " + p.url))}`);

  // ── 2. Cambia el modo y se acepta ──────────────────────────────────────────
  respuesta = true;
  await escenario({ guardadoSolo: false, eligeSolo: true });
  chequear("decir que sí guarda", patchs().length === 1);
  chequear("el modo nuevo viaja en el payload",
    (patchs()[0] || {}).body?.prompt_standalone === true,
    JSON.stringify((patchs()[0] || {}).body?.prompt_standalone));

  // ── 3. El camino inverso también avisa ─────────────────────────────────────
  await escenario({ guardadoSolo: true, eligeSolo: false });
  chequear("volver a las dos capas también pregunta", preguntas.length === 1);
  chequear("y avisa de la repetición",
    (preguntas[0] || {}).text?.includes("dos veces"), (preguntas[0] || {}).text);
  chequear("el modo nuevo viaja en el payload",
    (patchs()[0] || {}).body?.prompt_standalone === false);

  // ── 4. Sin tocar el modo, no molesta ───────────────────────────────────────
  await escenario({ guardadoSolo: true, eligeSolo: true });
  chequear("guardar sin cambiar el modo NO pregunta", preguntas.length === 0,
    `preguntas = ${preguntas.length}`);
  chequear("y guarda igual", patchs().length === 1);

  // ── 5. Palabra clave: el prompt no se usa, no hay nada que avisar ──────────
  await escenario({ guardadoSolo: false, eligeSolo: true, keyword: true });
  chequear("con palabra clave prendida NO pregunta", preguntas.length === 0);

  // ── 6. Alta de cliente: el modo es la decisión que se está tomando ─────────
  await escenario({ guardadoSolo: false, eligeSolo: true, nuevo: true });
  chequear("crear un cliente NO pregunta", preguntas.length === 0);
  chequear("y el alta viaja con el modo elegido",
    (patchs()[0] || {}).body?.prompt_standalone === true);

  console.log();
  if (fallos.length) {
    console.log(`${fallos.length} FALLA(S):`);
    for (const f of fallos) console.log("  - " + f);
    process.exit(1);
  }
  console.log("Todo OK");
})();
