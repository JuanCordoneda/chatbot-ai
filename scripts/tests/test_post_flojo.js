/**
 * "Post flojo cada tanto" (pedido de Lautaro, Growi): con el sorteo uniforme un
 * cliente de 5k–8k likes nunca tenía un post de 3k, y un perfil real sí los tiene.
 *
 * Lo que se afirma acá, corriendo las funciones REALES:
 *
 *   app.js (herramienta)
 *   - sin post flojo, la cantidad sale dentro del rango de la ficha, como siempre;
 *   - con post flojo, sale entre pct_min% y pct_max% del MÍNIMO del rango, y se
 *     marca como flojo (la orden lleva el cartel para que nadie la "corrija");
 *   - un rango chiquito no deja un rango vacío ni cantidades en 0;
 *
 *   admin.js (ficha)
 *   - prendido, viaja dentro de `ranges.bajon` con números;
 *   - en "Nunca", no viaja nada;
 *   - porcentajes fuera de 10–95 o invertidos frenan el guardado.
 *
 * Correr:  node scripts/tests/test_post_flojo.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "webService", "static");

const fallos = [];
function chequear(caso, cond, detalle = "") {
  console.log((cond ? "  OK   " : "  FALLA") + ` ${caso}` + (detalle ? ` — ${detalle}` : ""));
  if (!cond) fallos.push(caso);
}

function contexto(archivo, extra = {}) {
  const nodos = {};
  const nuevoNodo = () => ({
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    querySelector: () => nuevoNodo(), querySelectorAll: () => [],
    closest: () => null, scrollIntoView() {}, focus() {}, click() {},
    insertAdjacentHTML() {}, setAttribute() {}, getAttribute: () => null, remove() {},
    style: {}, dataset: {}, value: "", textContent: "", innerHTML: "",
    disabled: false, checked: false, files: [], options: [], add() {},
  });
  const porId = (id) => (nodos[id] = nodos[id] || nuevoNodo());
  const sandbox = {
    document: {
      getElementById: porId, querySelector: () => nuevoNodo(), querySelectorAll: () => [],
      createElement: () => nuevoNodo(), addEventListener() {}, removeEventListener() {},
      body: nuevoNodo(), documentElement: nuevoNodo(), cookie: "", readyState: "complete",
    },
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: "test" },
    location: { href: "http://test/", search: "", pathname: "/" },
    history: { replaceState() {}, pushState() {} },
    URLSearchParams, AbortController, scrollTo() {}, addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    requestAnimationFrame: (f) => setTimeout(f, 0),
    alert() {}, confirm: () => true, prompt: () => null,
    Image: function () { return nuevoNodo(); },
    Option: function (t, v) { return { text: t, value: v }; },
    IS_ADMIN: true,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    ...extra,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(STATIC, archivo), "utf8"), sandbox, { filename: archivo });
  return { sandbox, porId };
}

(async () => {
  // ── app.js: el sorteo ──────────────────────────────────────────────────────
  const { sandbox: app } = contexto("app.js");
  app._clientIg = "";      // sin cliente: no consulta cantidades usadas

  const r = { min: 5000, max: 8000 };
  let fuera = 0, marcados = 0;
  app._postBajon = null;
  for (let i = 0; i < 300; i++) {
    const t = await app._tirarDelRango("likes", r);
    if (t.val < 5000 || t.val > 8000) fuera++;
    if (t.flojo) marcados++;
  }
  chequear("sin post flojo: siempre dentro de 5.000–8.000", fuera === 0, `${fuera} fuera`);
  chequear("sin post flojo: no marca nada", marcados === 0);

  app._postBajon = { pct_min: 50, pct_max: 80 };
  fuera = 0; marcados = 0;
  for (let i = 0; i < 300; i++) {
    const t = await app._tirarDelRango("likes", r);
    if (t.val < 2500 || t.val > 4000) fuera++;
    if (t.flojo) marcados++;
  }
  chequear("post flojo 50–80%: siempre entre 2.500 y 4.000", fuera === 0, `${fuera} fuera`);
  chequear("post flojo: la tirada se marca", marcados === 300);

  const chico = await app._tirarDelRango("shares", { min: 1, max: 3 });
  chequear("rango chiquito: nunca 0", chico.val >= 1 && chico.min <= chico.max, JSON.stringify(chico));

  // ── admin.js: la ficha ─────────────────────────────────────────────────────
  const pedidos = [];
  const { sandbox: adm, porId } = contexto("admin.js");
  Object.assign(adm, {
    validarIg: () => "", revisarRangos: () => true, revisarComentarios: () => true,
    leerRangosDOM: () => ({ likes: [{ min: "5000", max: "8000" }] }),
    leerComentariosDOM: () => ({}), pintarCabeceraCliente() {}, renderVentaSelect() {},
    actualizarVentaHint() {}, loadClients() {}, toast() {}, closeMo() {},
    ventaById: () => ({ idvendedor: "12" }), soloPrompt: () => false,
    confirmDialog: () => Promise.resolve(true),
    api: (metodo, url, payload) => { pedidos.push({ metodo, payload }); return Promise.resolve({ client: {} }); },
  });
  vm.runInContext("clientsCache = []; sistemaCache = []; pedidoEnCurso = null;", adm);

  async function guardar(cada, lo, hi) {
    pedidos.length = 0;
    porId("client-id").value = "";
    porId("client-ig").value = "clientedeprueba";
    porId("bajon-cada").value = String(cada);
    porId("bajon-pct-min").value = String(lo);
    porId("bajon-pct-max").value = String(hi);
    await adm.saveClient();
    return pedidos[0];
  }

  let p = await guardar(6, 50, 80);
  chequear("prendido: viaja en ranges.bajon con números",
    JSON.stringify(p && p.payload.ranges.bajon) === JSON.stringify({ cada: 6, pct_min: 50, pct_max: 80 }),
    JSON.stringify(p && p.payload.ranges));
  chequear("prendido: los rangos siguen viajando", !!(p && p.payload.ranges.likes));

  p = await guardar(0, 50, 80);
  chequear("'Nunca': no viaja bajon", p && !("bajon" in p.payload.ranges), JSON.stringify(p && p.payload.ranges));

  p = await guardar(6, 5, 80);
  chequear("porcentaje fuera de rango: no guarda", !p);
  p = await guardar(6, 80, 50);
  chequear("porcentajes invertidos: no guarda", !p);

  console.log();
  if (fallos.length) {
    console.log(`${fallos.length} FALLA(S):`);
    for (const f of fallos) console.log("  - " + f);
    process.exit(1);
  }
  console.log("Todo OK");
})().catch((e) => { console.error(e); process.exit(1); });
