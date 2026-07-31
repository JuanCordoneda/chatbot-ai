"""Copia los PROMPTS de producción al sandbox local, para poder probar contra
datos reales sin tocar prod.

Qué hace:
  - Lee de prod (SOLO SELECT): cuentas y clientes con su prompt.
  - Escribe en el sandbox: crea/actualiza esas cuentas y clientes.

Qué NO hace, a propósito:
  - No escribe NUNCA en prod. Si detecta que el destino es la misma base que el
    origen, aborta.
  - No copia credenciales: password del CRM, idvendedor, idventa, proxy y demás
    quedan afuera. Acá sólo interesan los prompts, y bajar secretos de prod a una
    base local es un riesgo que no hace falta correr.
  - No copia usuarios ni eventos de uso.

Uso (desde la raíz del repo, con el stack local levantado):

    PROD_DATABASE_URL='postgresql://...'  \\
      docker compose -p chatbot-ai run --rm --no-deps \\
        -e PROD_DATABASE_URL -v "$PWD:/repo" \\
        --entrypoint python web-service /repo/scripts/traer_prompts_prod.py

  El destino sale de DATABASE_URL (la del .env, que apunta al contenedor `db`).
  Se puede pisar con SANDBOX_DATABASE_URL.

  En Railway usá la URL PÚBLICA de la base (DATABASE_PUBLIC_URL): la interna
  (*.railway.internal) no resuelve desde fuera de su red.

Opciones:
  --dry-run    Muestra qué copiaría, sin escribir nada.
"""
import json
import os
import sys

from sqlalchemy import create_engine, text


def _norm(url: str) -> str:
    """postgres:// es un alias viejo que SQLAlchemy 2.x ya no acepta."""
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _clave(url: str) -> str:
    """Identidad de la base (host/puerto/nombre), sin credenciales, para poder
    comparar origen y destino sin imprimir secretos."""
    from urllib.parse import urlparse
    u = urlparse(url)
    return f"{u.hostname}:{u.port or 5432}{u.path}"


def main() -> int:
    dry = "--dry-run" in sys.argv

    prod = _norm(os.environ.get("PROD_DATABASE_URL", ""))
    sandbox = _norm(os.environ.get("SANDBOX_DATABASE_URL") or os.environ.get("DATABASE_URL", ""))

    if not prod:
        print("ERROR: falta PROD_DATABASE_URL (la URL pública de la base de prod).")
        return 2
    if not sandbox:
        print("ERROR: falta DATABASE_URL / SANDBOX_DATABASE_URL (la base destino).")
        return 2
    if _clave(prod) == _clave(sandbox):
        print("ERROR: origen y destino son la MISMA base. Abortado para no tocar prod.")
        return 2

    print(f"origen  (prod, solo lectura): {_clave(prod)}")
    print(f"destino (sandbox)           : {_clave(sandbox)}")
    print()

    # La conexión a prod se abre en modo solo-lectura a nivel sesión: si algo en
    # este script intentara escribir, Postgres lo rechaza.
    src = create_engine(prod, connect_args={"options": "-c default_transaction_read_only=on"})
    dst = create_engine(sandbox)

    with src.connect() as c:
        cuentas = c.execute(text(
            "SELECT id, name, slug, active, status FROM accounts ORDER BY id"
        )).mappings().all()
        clientes = c.execute(text(
            "SELECT account_id, ig_username, display_name, prompt, status, gender, ranges "
            "FROM clients ORDER BY account_id, ig_username"
        )).mappings().all()

    generico = [x for x in clientes if x["ig_username"] == "__generico__"]
    normales = [x for x in clientes if x["ig_username"] != "__generico__"]

    print(f"En prod: {len(cuentas)} cuenta(s), {len(normales)} cliente(s) "
          f"+ {len(generico)} fila(s) de genérico.")
    print()
    por_cuenta = {a["id"]: a["name"] for a in cuentas}
    for x in normales:
        print(f"  [{por_cuenta.get(x['account_id'], '?')}] @{x['ig_username']:<22} "
              f"{len(x['prompt'] or ''):>6} car. de prompt")
    if generico:
        base = max(len(g["prompt"] or "") for g in generico)
        print(f"  [sistema]  __generico__{'':<12} {base:>6} car. de prompt")
    print()

    if dry:
        print("--dry-run: no se escribió nada.")
        return 0

    with dst.begin() as c:
        for a in cuentas:
            # Se respetan los ids de prod para que los clientes queden colgando
            # de la cuenta correcta. Sin credenciales del CRM (ver docstring).
            c.execute(text("""
                INSERT INTO accounts (id, name, slug, active, status, created_at)
                VALUES (:id, :name, :slug, :active, :status, NOW())
                ON CONFLICT (id) DO UPDATE
                   SET name = EXCLUDED.name, status = EXCLUDED.status
            """), dict(a))
        for x in clientes:
            # `ranges` es una columna JSON: el driver no sabe adaptar un dict de
            # Python en SQL crudo, así que se manda serializado y se castea.
            fila = dict(x)
            fila["ranges"] = json.dumps(fila["ranges"]) if fila["ranges"] is not None else None
            c.execute(text("""
                INSERT INTO clients (account_id, ig_username, display_name, prompt,
                                     status, gender, ranges, created_at, updated_at)
                VALUES (:account_id, :ig_username, :display_name, :prompt,
                        :status, :gender, CAST(:ranges AS json), NOW(), NOW())
                ON CONFLICT (account_id, ig_username) DO UPDATE
                   SET display_name = EXCLUDED.display_name,
                       prompt       = EXCLUDED.prompt,
                       status       = EXCLUDED.status,
                       gender       = EXCLUDED.gender,
                       ranges       = EXCLUDED.ranges,
                       updated_at   = NOW()
            """), fila)
        # Las secuencias quedan atrás al insertar ids explícitos: sin esto, el
        # próximo alta en el sandbox choca con un id ya usado.
        c.execute(text("SELECT setval('accounts_id_seq', (SELECT MAX(id) FROM accounts))"))
        c.execute(text("SELECT setval('clients_id_seq',  (SELECT MAX(id) FROM clients))"))

    print(f"Listo: {len(cuentas)} cuenta(s) y {len(clientes)} cliente(s) en el sandbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
