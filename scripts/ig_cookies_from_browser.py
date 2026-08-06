#!/usr/bin/env python3
"""
Saca las cookies de Instagram del navegador de esta máquina y arma
INSTAGRAM_COOKIES_JSON. Alternativa a set_ig_cookies.py para no tener que abrir
DevTools ni copiar valores a mano.

NO usa usuario ni contraseña: lee la sesión que ya tenés abierta en el navegador.
El scraper tampoco se loguea nunca — solo manda cookies (ver _load_ig_cookies en
post_processor.py).

Requiere `pip install browser-cookie3`.

Uso:
    # detecta el navegador, verifica de qué cuenta es la sesión y escribe el .env
    python scripts/ig_cookies_from_browser.py

    # elegir navegador y ver qué encontraría, sin escribir nada
    python scripts/ig_cookies_from_browser.py --browser chrome --dry-run

    # para producción, sin que el valor pase por el historial del shell:
    python scripts/ig_cookies_from_browser.py --stdout --no-env \
      | railway variables --set-from-stdin INSTAGRAM_COOKIES_JSON --service openai

En macOS, leer las cookies de Chrome pide acceso al Keychain: va a aparecer un
diálogo del sistema. Firefox no lo pide.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from set_ig_cookies import CLAVES, VARIABLE, escribir_env  # noqa: E402

# Sesión de @crowagency.ofc, la que quedó filtrada en el historial del repo. Si
# el navegador todavía tiene ESA sesión, escribirla sería volver al problema que
# estamos resolviendo, así que se corta salvo --force.
DS_USER_ID_CROW = "292597635"

NAVEGADORES = ("chrome", "firefox", "safari", "brave", "edge", "chromium", "opera")


def leer(navegador: str) -> dict:
    """Cookies de instagram.com de ese navegador. {} si no se puede leer."""
    import browser_cookie3

    fn = getattr(browser_cookie3, navegador, None)
    if fn is None:
        return {}
    try:
        tarro = fn(domain_name="instagram.com")
    except Exception as e:
        print(f"  {navegador}: no se pudo leer ({type(e).__name__}: {e})", file=sys.stderr)
        return {}
    return {c.name: c.value for c in tarro}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--browser", choices=NAVEGADORES,
                   help="de dónde leer (default: probar todos y usar el primero logueado)")
    p.add_argument("--env", default=".env")
    p.add_argument("--no-env", action="store_true", help="no tocar el .env")
    p.add_argument("--stdout", action="store_true", help="imprimir el JSON (para piping)")
    p.add_argument("--dry-run", action="store_true", help="solo informar, no escribir")
    p.add_argument("--force", action="store_true",
                   help="aceptar incluso la sesión de crowagency")
    args = p.parse_args()

    try:
        import browser_cookie3  # noqa: F401
    except ImportError:
        sys.exit("falta la dependencia: pip install browser-cookie3")

    candidatos = [args.browser] if args.browser else list(NAVEGADORES)
    print("Buscando una sesión de Instagram abierta...", file=sys.stderr)

    encontrado = None
    for navegador in candidatos:
        ck = leer(navegador)
        if not ck:
            continue
        if not ck.get("sessionid"):
            print(f"  {navegador}: hay cookies de instagram.com pero sin sessionid "
                  "(no hay sesión abierta)", file=sys.stderr)
            continue
        uid = ck.get("ds_user_id", "")
        etiqueta = ("crowagency.ofc — la cuenta que estamos sacando"
                    if uid == DS_USER_ID_CROW else f"ds_user_id={uid or '?'}")
        print(f"  {navegador}: sesión encontrada ({etiqueta})", file=sys.stderr)
        if uid == DS_USER_ID_CROW and not args.force:
            print("    la salteo: usarla sería volver a la sesión filtrada. "
                  "Logueate con la otra cuenta, o pasá --force si es a propósito.",
                  file=sys.stderr)
            continue
        encontrado = (navegador, ck)
        break

    if not encontrado:
        sys.exit("\nNo encontré ninguna sesión usable. Logueate en instagram.com con "
                 "la cuenta que querés usar y volvé a correr esto.\n"
                 "(Si usás Chrome en macOS y cancelaste el diálogo del Keychain, "
                 "probá --browser firefox.)")

    navegador, ck = encontrado
    cookies = {k: ck[k] for k in CLAVES if ck.get(k)}
    faltan = [k for k in CLAVES if k not in cookies]

    print(f"\nDe {navegador}: {len(cookies)} cookies ({', '.join(cookies)})", file=sys.stderr)
    if faltan:
        print(f"aviso: sin {', '.join(faltan)}. Funciona, pero Instagram bloquea "
              "más seguido a los pedidos con menos cookies.", file=sys.stderr)
    print(f"cuenta: ds_user_id={cookies.get('ds_user_id', '?')} "
          "— confirmá que sea la que esperabas antes de generar.", file=sys.stderr)

    valor = json.dumps(cookies, separators=(",", ":"))
    if args.dry_run:
        print(f"\n--dry-run: no escribí nada. Serían {len(valor)} chars en {VARIABLE}.",
              file=sys.stderr)
        return
    if not args.no_env:
        escribir_env(args.env, valor)
        print("Para que el contenedor la tome: docker compose -p chatbot-ai "
              "up -d --force-recreate openai-service", file=sys.stderr)
    if args.stdout:
        print(valor)


if __name__ == "__main__":
    main()
