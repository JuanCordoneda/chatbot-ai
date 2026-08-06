"""Saca las capturas de la guía (/ayuda) y las deja en
webService/static/onboarding/paso-N.png (tema oscuro) y paso-N-light.png.

    python3 harness.py 8899 &
    python3 shoot.py

Cada captura se toma en una ventana más alta de lo necesario y después se
recorta al contenido (el fondo es plano), así ningún paso queda cortado ni con
medio metro de vacío abajo. Correr esto de nuevo después de tocar el panel es lo
que mantiene la guía al día.
"""
import os, subprocess
from PIL import Image, ImageChops

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE = "http://127.0.0.1:8899"
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(RAIZ, "webService", "static", "onboarding")
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_raw")

# (archivo, ruta, shot, ancho, alto de ventana, recortar al contenido)
SHOTS = [
    ("paso-1", "/mis-clientes", "lista",             1460, 980, False),
    ("paso-2", "/mis-clientes", "ficha-nueva",       1340, 1300, True),
    ("paso-3", "/mis-clientes", "ficha:identidad",   1000, 700, True),
    ("paso-4", "/mis-clientes", "ficha:prompt",      1180, 1000, True),
    ("paso-5", "/mis-clientes", "ficha:calidad",      820, 500, True),
    ("paso-6", "/mis-clientes", "ficha:comentarios",  900, 700, True),
    ("paso-7", "/mis-clientes", "ficha:trafico",      900, 1000, True),
    ("paso-8", "/",             "gen",               1460, 980, True),
]

MAX_W = 1400   # ancho final (se captura a 2x y se baja a esto)


def recortar(img, pad=22):
    """Recorta los márgenes de color plano y deja un margen parejo."""
    fondo = img.getpixel((2, 2))
    base = Image.new(img.mode, img.size, fondo)
    bbox = ImageChops.difference(img, base).getbbox()
    if not bbox:
        return img
    x0, y0, x1, y1 = bbox
    return img.crop((max(0, x0 - pad), max(0, y0 - pad),
                     min(img.width, x1 + pad), min(img.height, y1 + pad)))


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    for nombre, ruta, shot, w, h, crop in SHOTS:
        for tema in ("dark", "light"):
            salida = nombre + ("" if tema == "dark" else "-light")
            tmp = os.path.join(TMP, salida + ".png")
            subprocess.run([
                CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--force-device-scale-factor=2", "--virtual-time-budget=9000",
                f"--window-size={w},{h}", f"--screenshot={tmp}",
                f"{BASE}{ruta}?shot={shot}&tema={tema}",
            ], capture_output=True)
            img = Image.open(tmp).convert("RGB")
            if crop:
                img = recortar(img)
            if img.width > MAX_W:
                img = img.resize((MAX_W, round(img.height * MAX_W / img.width)), Image.LANCZOS)
            destino = os.path.join(OUT, salida + ".png")
            img.save(destino, optimize=True)
            print(f"{salida}: {img.width}x{img.height}  {os.path.getsize(destino)//1024} KB")


if __name__ == "__main__":
    main()
