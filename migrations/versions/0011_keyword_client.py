"""Cliente reservado "__keyword__" (prompt maestro del modo keyword)

El modo keyword es otra herramienta: el vendedor escribe una palabra ("CLAUDE",
"PROMPTS") y salen N comentarios que son SOLO esa palabra, variando
mayúsculas/minúsculas, como la gente que comenta una keyword para que el bot del
creador le mande un PDF. Su prompt no se mezcla con el genérico ni con el del
cliente: es autónomo, y vive en la DB para que el admin lo edite desde el panel
(en prod los prompts salen de la DB).

Mismo patrón que 0008: idempotente, una fila por cuenta, y si el .txt no está en
el contenedor que corre la migración no falla (el cliente queda con prompt vacío
y ai_generator cae al archivo de la imagen).

Revision ID: 0011_keyword_client
Revises: 0010_client_quality
Create Date: 2026-08-04
"""
import os

from alembic import op
import sqlalchemy as sa

revision = "0011_keyword_client"
down_revision = "0010_client_quality"
branch_labels = None
depends_on = None

KEYWORD_IG = "__keyword__"
KEYWORD_NAME = "Palabra clave (modo keyword)"


def _prompt_text() -> str:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (os.path.join(here, "prompts", "keyword.txt"),
              os.path.join(here, "openAIService", "prompts", "keyword.txt"),
              "/app/prompts/keyword.txt"):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    print("[0011] AVISO: no encontré prompts/keyword.txt; creo el cliente con prompt vacío")
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
    """), {"ig": KEYWORD_IG}).fetchall()
    for (account_id,) in faltantes:
        conn.execute(sa.text("""
            INSERT INTO clients (account_id, ig_username, display_name, prompt, status,
                                 created_at, updated_at)
            VALUES (:acc, :ig, :name, :prompt, 'active', NOW(), NOW())
        """), {"acc": account_id, "ig": KEYWORD_IG, "name": KEYWORD_NAME, "prompt": prompt})
    print(f"[0011] cliente keyword creado en {len(faltantes)} cuenta(s)")


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM clients WHERE ig_username = :ig"), {"ig": KEYWORD_IG})
