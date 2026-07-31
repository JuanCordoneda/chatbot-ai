"""Compara la MISMA tanda generada en pro y en estándar, para calibrar.

El prompt de los clientes está calibrado contra el modelo pro (con thinking
adaptativo encima). El liviano puede repetir más entre comentarios o achatar la
variedad de voces — que es justo el "bot-feel" que se venía peleando. Antes de
dejar un cliente en estándar, conviene mirar las dos tandas una al lado de la
otra con un post real de esa cuenta.

Uso:
    python scripts/comparar_calidad.py --cliente peterjfournier \\
        --caption "texto del post" [--video] [--n 2]

    --n: cuántas tandas por calidad (2 o 3 ayuda a ver si repite entre tandas).

Corre contra la API de verdad: gasta tokens. No usa la DB para escribir nada.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "openAIService"))

from modules.ai_generator import generar_comentarios, GENERIC_CLIENT_ID  # noqa: E402


def _cliente(ig: str):
    """(client_id, gender) del cliente pedido. Sin DB o sin cliente, cae en el
    genérico, que es exactamente lo que usaría un post sin cliente asignado."""
    try:
        from common import repository as _repo
        row = _repo.get_client_by_ig_username(ig) if ig else None
    except Exception as e:
        print(f"[warn] sin DB ({e}): uso el cliente genérico", file=sys.stderr)
        row = None
    if not row:
        return GENERIC_CLIENT_ID, None
    return row["ig_username"], row.get("gender")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cliente", default="", help="@usuario de IG del cliente")
    p.add_argument("--caption", required=True, help="Texto del post")
    p.add_argument("--transcription", default="", help="Transcripción del audio, si es video")
    p.add_argument("--video", action="store_true")
    p.add_argument("--n", type=int, default=1, help="Tandas por calidad")
    args = p.parse_args()

    client_id, gender = _cliente(args.cliente.strip().lstrip("@").lower())
    print(f"cliente={client_id} genero={gender or 'mixto'}\n")

    for quality in ("pro", "standard"):
        for i in range(1, args.n + 1):
            print(f"{'=' * 70}\n{quality.upper()}  ·  tanda {i}/{args.n}\n{'=' * 70}")
            comentarios = generar_comentarios(
                args.caption, [], client_id,
                transcription=args.transcription,
                is_video=args.video,
                client_gender=gender,
                client_quality=quality,
            )
            for c in comentarios:
                print(f"  {c}")
            print(f"\n  → {len(comentarios)} comentarios\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
