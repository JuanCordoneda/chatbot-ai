import sys; sys.path.insert(0,'/app')
from common import repository as repo
from common.db import session_scope
from common.models import Account
FALLOS=[]
def check(n,c,d=""):
    print(("  OK   " if c else "  FALLA")+f" {n}"+(f"\n         → {d}" if d and not c else ""))
    if not c: FALLOS.append(n)

with session_scope() as s:
    a = s.query(Account).order_by(Account.id).first()
    aid, previo = a.id, a.crm_idvendedor
    a.crm_idvendedor = None

check("guarda cuando estaba vacío", repo.guardar_idvendedor(aid, "702") is True)
with session_scope() as s:
    check("quedó en la base", s.query(Account).filter(Account.id==aid).first().crm_idvendedor=="702")
check("no reescribe si es el mismo", repo.guardar_idvendedor(aid, "702") is False)
check("ignora vacío", repo.guardar_idvendedor(aid, "") is False)
check("ignora None", repo.guardar_idvendedor(aid, None) is False)
check("actualiza si cambia", repo.guardar_idvendedor(aid, "703") is True)
with session_scope() as s:
    check("con el valor nuevo", s.query(Account).filter(Account.id==aid).first().crm_idvendedor=="703")
    s.query(Account).filter(Account.id==aid).first().crm_idvendedor = previo   # restaurar
print("\n"+("TODO OK" if not FALLOS else f"FALLARON: {FALLOS}"))
sys.exit(1 if FALLOS else 0)
