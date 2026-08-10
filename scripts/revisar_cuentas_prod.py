"""Radiografía de las cuentas en producción: ¿a nombre de quién sale cada orden?

Responde la pregunta que disparó todo esto: si un vendedor manda una orden, ¿se
carga a SU nombre o termina en la cuenta del dueño de la agencia?

SOLO LEE. No hay un solo INSERT/UPDATE en este archivo, y la conexión se abre en
modo read-only para que no pueda escribir ni por error.

Uso (desde la raíz del repo, con el stack local levantado):

    PROD_DATABASE_URL='postgresql://...' \\
      docker compose -p chatbot-ai run --rm --no-deps \\
        -e PROD_DATABASE_URL -e GROWI_IDVENDEDOR -e GROWI_IDVENTA \\
        -v "$PWD:/repo" --entrypoint python web-service \\
        /repo/scripts/revisar_cuentas_prod.py

  En Railway usá la URL PÚBLICA de la base (DATABASE_PUBLIC_URL): la interna
  (*.railway.internal) no resuelve desde fuera de su red.

  GROWI_IDVENDEDOR / GROWI_IDVENTA son los del .env de PROD (los del dueño). Si
  no se pasan, se toman los del .env local, que normalmente son los mismos.
"""
import os
import sys

from sqlalchemy import create_engine, text


def _norm(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main() -> int:
    prod = _norm(os.environ.get("PROD_DATABASE_URL", ""))
    if not prod:
        print("Falta PROD_DATABASE_URL (la URL pública de la base de prod).")
        return 2

    env_vendedor = (os.environ.get("GROWI_IDVENDEDOR", "") or "").strip()
    env_venta = (os.environ.get("GROWI_IDVENTA", "32600") or "").strip()

    from urllib.parse import urlparse
    u = urlparse(prod)
    print(f"Base: {u.hostname}:{u.port or 5432}{u.path}  (solo lectura)")
    print(f"IDs del dueño según el .env: idvendedor={env_vendedor or '—'} "
          f"idventa={env_venta or '—'}\n")

    engine = create_engine(prod, pool_pre_ping=True,
                           connect_args={"options": "-c default_transaction_read_only=on"})

    with engine.connect() as cx:
        filas = cx.execute(text("""
            select id, name, status, active,
                   coalesce(crm_email, '')      as crm_email,
                   coalesce(crm_idvendedor, '') as idvendedor,
                   coalesce(crm_idventa, '')    as idventa
              from accounts
             order by id
        """)).mappings().all()

        print(f"{'id':>4}  {'cuenta':<22} {'estado':<10} {'crm_email':<32} "
              f"{'idvend':<8} {'idventa':<8} veredicto")
        print("-" * 118)

        problemas = []
        for f in filas:
            estado = f["status"] or ("activa" if f["active"] else "inactiva")
            vered = []
            if not f["crm_email"]:
                vered.append("BLOQUEADA: sin CRM propio")
                problemas.append((f["id"], f["name"], "sin crm_email"))
            if env_vendedor and f["idvendedor"] == env_vendedor and f["id"] != 1:
                vered.append("OJO: usa el idvendedor del dueño")
                problemas.append((f["id"], f["name"], "idvendedor del dueño"))
            if env_venta and f["idventa"] == env_venta and f["id"] != 1:
                vered.append("OJO: usa la campaña del dueño")
                problemas.append((f["id"], f["name"], "idventa del dueño"))
            if not f["idvendedor"] and not f["idventa"]:
                vered.append("sin ids: depende de que el cliente tenga campaña")
            print(f"{f['id']:>4}  {(f['name'] or '')[:22]:<22} {estado[:10]:<10} "
                  f"{(f['crm_email'] or '—')[:32]:<32} "
                  f"{(f['idvendedor'] or '—'):<8} {(f['idventa'] or '—'):<8} "
                  f"{'; '.join(vered) or 'OK'}")

        # Emails de CRM repetidos: dos cuentas entrando al MISMO CRM es la
        # versión silenciosa del bug (las órdenes de una aparecen en la otra).
        repes = cx.execute(text("""
            select lower(crm_email) as email, count(*) as n,
                   string_agg(name, ', ' order by id) as cuentas
              from accounts
             where coalesce(crm_email, '') <> ''
             group by lower(crm_email)
            having count(*) > 1
        """)).mappings().all()
        if repes:
            print("\n⚠️  Cuentas distintas con el MISMO login de CRM:")
            for r in repes:
                print(f"   {r['email']}: {r['n']} cuentas → {r['cuentas']}")
                problemas.append((None, r["cuentas"], "comparten login de CRM"))

        # Órdenes encoladas sin cuenta: no se pueden reenviar solas.
        try:
            pend = cx.execute(text("""
                select estado, count(*) as n,
                       count(*) filter (where account_id is null) as sin_cuenta
                  from pending_orders
                 group by estado order by estado
            """)).mappings().all()
            if pend:
                print("\nCola de órdenes pendientes:")
                for p in pend:
                    extra = f"  ({p['sin_cuenta']} sin cuenta → van a revisión manual)" \
                        if p["sin_cuenta"] else ""
                    print(f"   {p['estado']}: {p['n']}{extra}")
        except Exception as e:
            print(f"\n(no pude leer pending_orders: {e})")

        # Clientes apuntados a una campaña a mano.
        try:
            cli = cx.execute(text("""
                select a.name as cuenta, c.ig_username, c.crm_idventa
                  from clients c join accounts a on a.id = c.account_id
                 where coalesce(c.crm_idventa, '') <> ''
                 order by a.name, c.ig_username
            """)).mappings().all()
            if cli:
                print(f"\nClientes con campaña asignada a mano: {len(cli)}")
                for c in cli[:15]:
                    marca = "  ← campaña del dueño" if c["crm_idventa"] == env_venta else ""
                    print(f"   {c['cuenta']} / @{c['ig_username']} → #{c['crm_idventa']}{marca}")
                if len(cli) > 15:
                    print(f"   … y {len(cli) - 15} más")
        except Exception as e:
            print(f"\n(no pude leer clients: {e})")

    print("\n" + "=" * 60)
    if problemas:
        print(f"HAY {len(problemas)} COSA(S) PARA ARREGLAR ANTES DE QUE MANDEN ÓRDENES:")
        for pid, nombre, que in problemas:
            print(f"  - cuenta {pid if pid is not None else '(varias)'} "
                  f"[{nombre}]: {que}")
    else:
        print("Todas las cuentas pueden cargar órdenes a su propio nombre.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
