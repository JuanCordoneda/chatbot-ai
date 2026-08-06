// Ayuda: lo poco que necesitan las dos guías. No cargan app.js ni admin.js.

// Tema: mismo mecanismo que el resto (clase .light + localStorage), para no
// perder la preferencia al entrar y salir de la ayuda.
function toggleTheme() {
  const isLight = document.body.classList.toggle("light");
  localStorage.setItem("theme", isLight ? "light" : "dark");
  document.getElementById("btn-theme").textContent = isLight ? "☾ Dark" : "☀ Light";
}
if (localStorage.getItem("theme") === "light") {
  document.body.classList.add("light");
  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("btn-theme");
    if (b) b.textContent = "☾ Dark";
  });
}

// Click en una captura: se abre en grande. Al ancho de la columna, las de
// pantalla completa no se leen.
function zoom(img) {
  document.getElementById("ay-zoom-img").src = img.src;
  document.getElementById("ay-zoom").classList.add("on");
}
function cerrarZoom() {
  document.getElementById("ay-zoom").classList.remove("on");
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarZoom(); });
