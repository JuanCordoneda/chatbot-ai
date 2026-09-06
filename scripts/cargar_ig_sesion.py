#!/usr/bin/env python3
"""
Carga la sesión de Instagram de un navegador DIRECTO en el panel, sin copiar y
pegar el sessionid a mano.

Es el atajo del flujo del panel (/admin → Instagram), no un camino aparte: pega
en el mismo endpoint, que valida la sesión contra Instagram antes de guardarla.
La diferencia con script_COOKIE.sh es la que importa: aquel sube a
INSTAGRAM_COOKIES_JSON, que es UNA variable y cada corrida pisa la anterior;
esto agrega una fila más, así que podés tener varias cuentas a la vez y que el
scraper rote solo cuando Instagram tumbe una.

La PRIMERA cuenta que cargues queda como principal (prioridad 0) y las
siguientes como respaldo, en orden. Se cambia después con el botón "Subir" del
panel.

Uso (el token interno sale de DATABASE_URL, por eso va con `railway run`):

    railway run --service openai -- .venv-ig/bin/python \\
        scripts/cargar_ig_sesion.py --browser safari --username los.chicos.lol

    railway run --service openai -- .venv-ig/bin/python \\
        scripts/cargar_ig_sesion.py --browser chrome --profile "Juan Cruz" \\
        --username script_jcc2

Con --dry-run dice qué cuenta subiría y no toca nada.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "scripts"))

from ig_cookies_from_browser import leer                            # noqa: E402

SERVICIO = os.environ.get("IG_ADMIN_URL",
                          "https://openai-production-531a.up.railway.app")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--browser", required=True)
    p.add_argument("--profile", default="", help="perfil de Chrome (carpeta o nombre)")
    p.add_argument("--username", default="", help="@usuario, para reconocerla en el panel")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    cookies = leer(args.browser, args.profile)
    if not cookies.get("sessionid"):
        sys.exit(f"no encontré una sesión de Instagram en {args.browser}"
                 f"{' (perfil ' + args.profile + ')' if args.profile else ''}")

    uid = cookies.get("ds_user_id", "?")
    print(f"sesión de {args.browser}{' / ' + args.profile if args.profile else ''}: "
          f"ds_user_id={uid}")
    if args.dry_run:
        print("--dry-run: no cargué nada.")
        return

    # El token sale de DATABASE_URL (ver common/interno.py): no hay que
    # configurar un secreto nuevo, pero SÍ hay que correr esto con
    # `railway run --service openai`, que es quien inyecta esa variable.
    from common.interno import token
    tok = token()
    if not tok:
        sys.exit("sin DATABASE_URL no puedo armar el token interno: corré esto con "
                 "`railway run --service openai -- ...`")

    cuerpo = json.dumps({
        "cookies": cookies,
        "username": args.username,
        "creada_por": os.environ.get("USER", "terminal"),
    }).encode()
    req = urllib.request.Request(
        f"{SERVICIO}/admin/ig-sesiones", data=cuerpo, method="POST",
        headers={"Content-Type": "application/json", "X-Interno": tok},
    )
    try:
        # El server la prueba contra Instagram antes de guardarla, y eso tarda.
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        detalle = e.read().decode(errors="replace")[:400]
        try:
            detalle = json.loads(detalle).get("error", detalle)
        except Exception:
            pass
        sys.exit(f"no se cargó: {detalle}")
    except Exception as e:
        sys.exit(f"no pude hablar con el servicio: {e!r}")

    s = d.get("sesion", {})
    print(f"cargada: @{s.get('username') or s.get('ds_user_id')} "
          f"(prioridad {s.get('prioridad')}, estado {s.get('estado')})")
    print("Mirala en /admin → Instagram.")


if __name__ == "__main__":
    main()
