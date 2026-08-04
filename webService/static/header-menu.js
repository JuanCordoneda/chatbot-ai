// Header en mobile: menú hamburguesa.
// Vive en un archivo aparte porque las tres pantallas (generador, followers y
// panel de clientes) comparten exactamente el mismo header.

(function () {
  const MOBILE = () => window.matchMedia("(max-width: 768px)").matches;

  // ── Menú hamburguesa ────────────────────────────────────────────────────
  // El header tiene hasta 6 botones. En un teléfono no entran, y esconderles el
  // texto los deja como una fila de íconos sin nombre. Se agrupan detrás del ☰
  // y ahí sí se muestran con su etiqueta.
  function armarMenu() {
    const acciones = document.querySelector(".header-actions");
    const grupo = document.querySelector(".header-btn-group");
    if (!acciones || !grupo || document.getElementById("btn-menu")) return;

    const btn = document.createElement("button");
    btn.id = "btn-menu";
    btn.className = "header-menu-btn";
    btn.type = "button";
    btn.setAttribute("aria-label", "Abrir menú");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", grupo.id || "header-btn-group");
    btn.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">' +
      '<path d="M2.5 4.5h13M2.5 9h13M2.5 13.5h13" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
      "</svg>";
    if (!grupo.id) grupo.id = "header-btn-group";
    acciones.insertBefore(btn, grupo);

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirMenu(!document.body.classList.contains("menu-abierto"));
    });

    // Elegir una opción cierra el menú. Los que navegan se lo llevan puesto,
    // pero el de tema y el de refrescar se quedan en la página.
    grupo.addEventListener("click", () => abrirMenu(false));

    // Un tap fuera cierra: es lo que todos intentan antes de buscar la X.
    document.addEventListener("click", (e) => {
      if (!document.body.classList.contains("menu-abierto")) return;
      if (grupo.contains(e.target) || btn.contains(e.target)) return;
      abrirMenu(false);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && document.body.classList.contains("menu-abierto")) {
        abrirMenu(false);
        btn.focus();
      }
    });

    // Al volver a un ancho de desktop el menú no tiene sentido: si quedó
    // abierto, los botones aparecerían dos veces.
    window.addEventListener("resize", () => {
      if (!MOBILE()) abrirMenu(false);
    });
  }

  function abrirMenu(abrir) {
    document.body.classList.toggle("menu-abierto", abrir);
    const btn = document.getElementById("btn-menu");
    if (btn) btn.setAttribute("aria-expanded", abrir ? "true" : "false");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", armarMenu);
  } else {
    armarMenu();
  }
})();
