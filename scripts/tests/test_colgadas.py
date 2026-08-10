import sys, time
sys.path.insert(0,'/app')
from datetime import timedelta
from common import repository as repo
from common.db import session_scope
from common.models import PendingOrder

FALLOS=[]
def check(n,c,d=""):
    print(("  OK   " if c else "  FALLA")+f" {n}"+(f"\n         → {d}" if d and not c else ""))
    if not c: FALLOS.append(n)

with session_scope() as s:
    s.query(PendingOrder).delete()

o = repo.encolar_orden("https://instagram.com/p/test", {"comentarios":["a"],"ordenes":[]},
                       account_id=None, user_id=None, client_ig_username="", error="prueba")
oid = o["id"]
tomada = repo.tomar_orden_para_reintentar()
check("la orden se reclama", tomada and tomada["id"]==oid, tomada)

with session_scope() as s:
    p = s.query(PendingOrder).get(oid)
    check("queda en 'enviando'", p.estado=="enviando", p.estado)
    check("con fecha límite futura", p.proximo_intento is not None, p.proximo_intento)
    limite = p.proximo_intento

# Todavía dentro del plazo: no se toca.
check("dentro del plazo no la rescata", repo.revisar_ordenes_colgadas()==0)

# Simulamos que el proceso murió: la fecha límite ya pasó.
with session_scope() as s:
    p = s.query(PendingOrder).get(oid)
    p.proximo_intento = p.proximo_intento - timedelta(minutes=30)

check("pasado el plazo la rescata", repo.revisar_ordenes_colgadas()==1)
with session_scope() as s:
    p = s.query(PendingOrder).get(oid)
    check("va a 'revisar', no a reintento automático", p.estado=="revisar", p.estado)
    check("con el motivo escrito", "colgado" in (p.ultimo_error or ""), p.ultimo_error)
    check("y sin próximo intento", p.proximo_intento is None, p.proximo_intento)

check("no la vuelve a tomar el worker", repo.tomar_orden_para_reintentar() is None)

with session_scope() as s:
    s.query(PendingOrder).delete()
print("\n"+("TODO OK" if not FALLOS else f"FALLARON: {FALLOS}"))
sys.exit(1 if FALLOS else 0)
