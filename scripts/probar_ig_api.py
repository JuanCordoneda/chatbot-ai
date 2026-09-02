#!/usr/bin/env python3
"""¿Por qué no anda el scrapeo de Instagram? Repite el pedido EXACTO que hace el
server (`_fetch_instagram_api`), pero desde esta Mac y con la sesión de Safari.

Sirve para separar tres causas que dan el mismo síntoma ("Instagram no devolvió
JSON") y que se arreglan de formas distintas:

  - JSON con datos  → la cookie sirve y el doc_id sigue vivo. El problema es
    DÓNDE corre el pedido: Instagram le contesta HTML a la IP del datacenter
    (Railway) y no a una casa. Renovar la cookie no lo arregla.
  - HTML "Page Not Found" → el doc_id que usamos lo retiró Instagram. Es un
    cambio de código, ninguna cookie lo arregla.
  - HTML de login → la sesión de Safari tampoco sirve (te deslogueaste).

Uso:
    .venv-ig/bin/python scripts/probar_ig_api.py [shortcode]

Necesita Terminal con Acceso Total al Disco para leer las cookies de Safari.
No imprime el valor de ninguna cookie.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DOC_ID = "10015901848480474"      # el mismo de post_processor._fetch_instagram_api
CLAVES = ["sessionid", "csrftoken", "ds_user_id", "mid", "ig_did", "datr"]


def cookies_de_safari() -> dict:
    import browser_cookie3
    tarro = browser_cookie3.safari(domain_name="instagram.com")
    return {c.name: c.value for c in tarro}


def main() -> None:
    shortcode = sys.argv[1] if len(sys.argv) > 1 else "DY5mFTuxsIO"
    try:
        ck = cookies_de_safari()
    except Exception as e:
        sys.exit(f"no pude leer las cookies de Safari ({type(e).__name__}: {e}).\n"
                 "Terminal necesita Acceso Total al Disco.")
    if not ck.get("sessionid"):
        sys.exit("Safari no tiene sesión de Instagram abierta.")
    print(f"sesión de Safari: ds_user_id={ck.get('ds_user_id', '?')} "
          f"({len(ck)} cookies, faltan: {[k for k in CLAVES if k not in ck] or 'ninguna'})")

    datos = urllib.parse.urlencode({
        "doc_id": DOC_ID,
        "variables": json.dumps({
            "shortcode": shortcode,
            "__relay_internal__pv__PolarisFeedShareMenurelayprovider": False,
        }),
    }).encode()
    pedido = urllib.request.Request(
        "https://www.instagram.com/graphql/query", data=datos,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "x-ig-app-id": "936619743392459",
            "x-csrftoken": ck.get("csrftoken", ""),
            "content-type": "application/x-www-form-urlencoded",
            "Referer": f"https://www.instagram.com/p/{shortcode}/",
            "Origin": "https://www.instagram.com",
            "Cookie": "; ".join(f"{k}={v}" for k, v in ck.items()),
        })

    try:
        with urllib.request.urlopen(pedido, timeout=20) as r:
            estado, cuerpo = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        estado, cuerpo = e.code, e.read().decode("utf-8", "replace")
    print(f"HTTP {estado}, {len(cuerpo)} bytes")

    try:
        media = (json.loads(cuerpo).get("data") or {}).get("xdt_shortcode_media")
    except ValueError:
        inicio = cuerpo[:120].replace("\n", " ").strip()
        print(f"NO es JSON. Empieza con: {inicio!r}")
        if "Page Not Found" in cuerpo:
            print("\n→ doc_id retirado por Instagram: hay que cambiarlo en el código.")
        else:
            print("\n→ Instagram te pide login/verificación: la sesión de Safari tampoco sirve.")
        sys.exit(1)

    if not media:
        print("JSON pero sin media (post privado, borrado o restringido).")
        sys.exit(1)
    print(f"JSON OK — owner={media.get('owner', {}).get('username')} "
          f"is_video={media.get('is_video')}")
    print("\n→ Desde esta Mac anda. Si en el server falla con la MISMA cookie, "
          "Instagram está bloqueando la IP de Railway, no la sesión.")


if __name__ == "__main__":
    main()
