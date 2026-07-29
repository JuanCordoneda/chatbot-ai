"""Cliente reservado "__generico__" (prompt de los posts sin cliente)

Los posts que no son de ningún cliente cargado usaban el prompt de Peter
Fournier, que arrastra sus personajes y sus @menciones a cuentas que no tienen
nada que ver. Ahora usan este cliente genérico, que se crea una vez por cuenta
para que sea editable desde el panel (en prod los prompts salen de la DB).

Idempotente: solo inserta en las cuentas que todavía no lo tienen. Si el archivo
prompts/generico.txt no está en el contenedor que corre la migración, no falla:
crea el cliente con prompt vacío y ai_generator cae igual al .txt de la imagen.

Revision ID: 0008_generic_client
Revises: 0007_account_status
Create Date: 2026-07-29
"""
import os

from alembic import op
import sqlalchemy as sa

revision = "0008_generic_client"
down_revision = "0007_account_status"
branch_labels = None
depends_on = None

GENERIC_IG = "__generico__"
GENERIC_NAME = "Genéricos (posts sin cliente)"


def _prompt_text() -> str:
    """El prompt vive en el repo; según el layout está en /app/prompts o en
    openAIService/prompts. Se busca en ambos."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (os.path.join(here, "prompts", "generico.txt"),
              os.path.join(here, "openAIService", "prompts", "generico.txt"),
              "/app/prompts/generico.txt"):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    print("[0008] AVISO: no encontré prompts/generico.txt; creo el cliente con prompt vacío")
    return ""


def upgrade() -> None:
    conn = op.get_bind()
    prompt = _prompt_text()
    faltantes = conn.execute(sa.text("""
        SELECT a.id FROM accounts a
        WHERE NOT EXISTS (
            SELECT 1 FROM clients c
            WHERE c.account_id = a.id AND c.ig_username = :ig
        )
    """), {"ig": GENERIC_IG}).fetchall()
    for (account_id,) in faltantes:
        conn.execute(sa.text("""
            INSERT INTO clients (account_id, ig_username, display_name, prompt, status,
                                 created_at, updated_at)
            VALUES (:acc, :ig, :name, :prompt, 'active', NOW(), NOW())
        """), {"acc": account_id, "ig": GENERIC_IG, "name": GENERIC_NAME, "prompt": prompt})
    print(f"[0008] cliente genérico creado en {len(faltantes)} cuenta(s)")


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM clients WHERE ig_username = :ig"), {"ig": GENERIC_IG})
