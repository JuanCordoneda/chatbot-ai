"""
Migra los datos existentes de Facu a la DB multi-tenant (TAREA 1).

Idempotente: se puede correr varias veces sin duplicar. No borra ningún archivo
(clients_map.json y prompts/*.txt quedan como fallback).

Qué crea, todo bajo la cuenta "Growi (Facu)":
  - la cuenta, con las credenciales del CRM tomadas del entorno (.env);
  - un cliente por cada entrada de clients_map.json, con su prompt (.txt o default);
  - los 2 usuarios actuales: growi (vendedor) y growi-admin (admin).

Uso (con DATABASE_URL y el .env cargados, tras `alembic upgrade head`):
    python scripts/seed_facu.py
"""
import json
import os
import sys

# raíz del repo importable (para `common`)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from werkzeug.security import generate_password_hash  # noqa: E402

from common.db import db_available, session_scope  # noqa: E402
from common.models import Account, User, Client  # noqa: E402


def _first_existing(*candidates: str) -> str:
    """Los datos de origen viven en distinto lugar según el layout:
      - repo local:   <root>/openAIService/clients_map.json, .../prompts
      - contenedor:   /app/clients_map.json, /app/prompts (imagen aplanada)
    Devuelve el primer candidato que exista (o el primero como fallback)."""
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


_CLIENTS_MAP = _first_existing(
    os.path.join(_ROOT, "openAIService", "clients_map.json"),
    os.path.join(_ROOT, "clients_map.json"),
)
_PROMPTS_DIR = _first_existing(
    os.path.join(_ROOT, "openAIService", "prompts"),
    os.path.join(_ROOT, "prompts"),
)

# Usuarios actuales hardcodeados en webService/app.py (misma password que hoy).
_LEGACY_USERS = [
    {"username": "growi",       "password": "growi2026", "role": "vendedor"},
    {"username": "growi-admin", "password": "growi2026", "role": "admin"},
]

FACU_SLUG = "growi-facu"


def _load_prompt_for(ig_username: str) -> str:
    key = ig_username.lower().replace(" ", "")
    p = os.path.join(_PROMPTS_DIR, "clients", f"{key}.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read()
    default = os.path.join(_PROMPTS_DIR, "default.txt")
    if os.path.exists(default):
        with open(default, encoding="utf-8") as f:
            return f.read()
    return ""


def _get_or_create_account(s) -> Account:
    acc = s.query(Account).filter(Account.slug == FACU_SLUG).first()
    if acc is None:
        acc = Account(name="Growi (Facu)", slug=FACU_SLUG, active=True)
        s.add(acc)
        s.flush()
        print(f"[seed] cuenta creada: {acc.name} (id={acc.id})")
    else:
        print(f"[seed] cuenta ya existía: {acc.name} (id={acc.id})")

    # Config del CRM desde el entorno actual (se actualiza siempre por si cambió
    # en el .env). La contraseña NO va: no se guarda en la base. La tipea el
    # vendedor al entrar y vive en memoria mientras dura su sesión.
    acc.crm_url = os.environ.get("GROWI_CRM_URL", "https://crm.growiagency.com")
    acc.crm_email = os.environ.get("GROWI_CRM_EMAIL", "")
    acc.crm_idvendedor = os.environ.get("GROWI_IDVENDEDOR", "")
    acc.crm_idventa = os.environ.get("GROWI_IDVENTA", "32600")
    acc.crm_proxy = os.environ.get("GROWI_HTTP_PROXY", "")
    acc.crm_disponible = os.environ.get("GROWI_DISPONIBLE", "150")
    return acc


def _seed_clients(s, account: Account):
    # RESEED_PROMPTS=1 refresca el prompt de los clientes YA existentes desde los
    # .txt (para empujar mejoras de prompt a producción sin pasar por el panel).
    # OJO: pisa lo que haya en la DB. Solo activarlo si el panel NO es la fuente
    # de verdad de los prompts (nadie los editó a mano ahí).
    reseed_prompts = os.environ.get("RESEED_PROMPTS", "").strip().lower() in ("1", "true", "yes")

    try:
        with open(_CLIENTS_MAP, encoding="utf-8") as f:
            clients_map = json.load(f)
    except Exception as e:
        print(f"[seed] no pude leer clients_map.json: {e}")
        clients_map = {}

    for ig_username, display_name in clients_map.items():
        key = ig_username.strip().lower()
        existing = s.query(Client).filter(
            Client.account_id == account.id, Client.ig_username == key
        ).first()
        if existing:
            if reseed_prompts:
                existing.prompt = _load_prompt_for(ig_username)
                print(f"[seed]   prompt refrescado: @{key}")
            else:
                print(f"[seed]   cliente ya existía: @{key}")
            continue
        s.add(Client(
            account_id=account.id,
            ig_username=key,
            display_name=display_name,
            prompt=_load_prompt_for(ig_username),
            status="active",
        ))
        print(f"[seed]   cliente creado: @{key} ({display_name})")


def _seed_users(s, account: Account):
    for u in _LEGACY_USERS:
        existing = s.query(User).filter(User.username == u["username"]).first()
        if existing:
            print(f"[seed]   usuario ya existía: {u['username']}")
            continue
        s.add(User(
            account_id=account.id,
            username=u["username"],
            password_hash=generate_password_hash(u["password"]),
            role=u["role"],
            active=True,
        ))
        print(f"[seed]   usuario creado: {u['username']} ({u['role']})")


def main():
    if not db_available():
        print("[seed] ERROR: DATABASE_URL no configurada. Nada que hacer.")
        sys.exit(1)
    with session_scope() as s:
        acc = _get_or_create_account(s)
        _seed_clients(s, acc)
        _seed_users(s, acc)
    print("[seed] listo.")


if __name__ == "__main__":
    main()
