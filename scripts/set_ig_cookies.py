#!/usr/bin/env python3
"""
Arma INSTAGRAM_COOKIES_JSON a partir de las cookies que copiás del navegador.

Existe para no escribir el JSON a mano (un `%3A` mal copiado o una coma de menos
y el scrapeo falla con un error que no dice nada) y para que el sessionid no pase
por el portapapeles más veces de las necesarias.

De dónde salen los valores: logueado en instagram.com con la cuenta que va a
scrapear → DevTools (Cmd+Option+I) → Application → Storage → Cookies →
https://www.instagram.com. OJO: `sessionid` es HttpOnly, así que NO aparece en
`document.cookie`; hay que leerlo de ese panel.

Uso:
    # actualiza el .env local (hace backup antes)
    python scripts/set_ig_cookies.py

    # además lo imprime para mandarlo a Railway, sin tocar el .env
    python scripts/set_ig_cookies.py --stdout --no-env

    # y para subirlo a producción sin que quede en el historial del shell:
    python scripts/set_ig_cookies.py --stdout --no-env \
      | railway variables --set-from-stdin INSTAGRAM_COOKIES_JSON --service openai

Lo único imprescindible es `sessionid`. El resto reduce las chances de que
Instagram marque el pedido como automatizado, así que conviene ponerlos.
"""
import argparse
import getpass
import json
import os
import shutil
import sys

# sessionid va primero y se pide oculto: es el que da acceso a la cuenta.
CLAVES = ["sessionid", "csrftoken", "ds_user_id", "mid", "ig_did", "datr"]
VARIABLE = "INSTAGRAM_COOKIES_JSON"


def pedir_cookies() -> dict:
    print("Pegá el valor de cada cookie (Enter vacío = omitir).\n"
          "El sessionid no se muestra al tipear.\n", file=sys.stderr)
    cookies = {}
    for clave in CLAVES:
        if clave == "sessionid":
            valor = getpass.getpass("sessionid (oculto): ").strip()
        else:
            valor = input(f"{clave}: ").strip()
        # Copiar desde DevTools a veces arrastra las comillas de alrededor.
        valor = valor.strip('"').strip("'")
        if valor:
            cookies[clave] = valor

    if not cookies.get("sessionid"):
        sys.exit("error: sin sessionid no hay sesión; el scrapeo va a fallar.")
    # Chequeo de forma, no de validez: el sessionid real viene URL-encodeado
    # (%3A como separador). Si no lo tiene, probablemente se copió la fila
    # equivocada (o el nombre en vez del valor).
    s = cookies["sessionid"]
    if "%3A" not in s and ":" not in s:
        print("aviso: el sessionid no tiene el formato habitual "
              "(<user_id>%3A<token>%3A...). Revisá que hayas copiado la columna "
              "Value de la fila sessionid.", file=sys.stderr)
    faltan = [k for k in CLAVES if k not in cookies]
    if faltan:
        print(f"aviso: sin {', '.join(faltan)}. Funciona, pero Instagram "
              "bloquea más seguido a los pedidos con menos cookies.", file=sys.stderr)
    return cookies


def escribir_env(ruta: str, valor: str) -> None:
    """Reemplaza (o agrega) la línea de la variable en el .env, con backup."""
    lineas = []
    if os.path.exists(ruta):
        shutil.copy2(ruta, ruta + ".bak")
        print(f"backup: {ruta}.bak", file=sys.stderr)
        with open(ruta, encoding="utf-8") as f:
            lineas = f.read().splitlines()

    nueva = f"{VARIABLE}={valor}"
    for i, linea in enumerate(lineas):
        if linea.split("=", 1)[0].strip() == VARIABLE:
            lineas[i] = nueva
            break
    else:
        lineas.append(nueva)

    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    print(f"{ruta}: {VARIABLE} actualizada", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default=".env", help="ruta del .env (default: .env)")
    p.add_argument("--no-env", action="store_true", help="no tocar el .env")
    p.add_argument("--stdout", action="store_true",
                   help="imprimir el JSON por stdout (para piping a railway)")
    args = p.parse_args()

    cookies = pedir_cookies()
    # separators sin espacios: queda en una sola línea, que es lo que necesita
    # tanto el .env como la variable de Railway.
    valor = json.dumps(cookies, separators=(",", ":"))

    if not args.no_env:
        escribir_env(args.env, valor)
    if args.stdout:
        print(valor)
    if args.no_env and not args.stdout:
        print("nada que hacer: --no-env sin --stdout", file=sys.stderr)

    print(f"\nlisto: {len(cookies)} cookies ({', '.join(cookies)})", file=sys.stderr)
    if not args.no_env:
        print("Para que el contenedor la tome: docker compose -p chatbot-ai "
              "up -d --force-recreate openai-service", file=sys.stderr)


if __name__ == "__main__":
    main()
