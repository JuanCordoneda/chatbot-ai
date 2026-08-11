"""Un envío que rebotó se puede volver a mandar desde la pantalla.

La traza de auditoría guarda las órdenes COMPLETAS, con sus comentarios adentro
(`request_payload.ordenes`). Durante mucho tiempo el código dio por sentado lo
contrario —"de un rebote quedó la traza pero no los comentarios"— y por eso esas
órdenes se mostraban sin botón: el vendedor tenía que rehacer el post entero.

Lo que este test cuida es la regla que protege la plata: se ofrece reintentar
solo cuando sabemos que el CRM NO insertó nada. enviar_trafico.php no es
idempotente, y remandar una tanda que entró se la cobra dos veces al cliente.

    PYTHONPATH=.:webService python3 scripts/tests/test_reintento_desde_traza.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webService"))

import app as web                                                   # noqa: E402

FALLOS = []


def check(nombre, cond, extra=""):
    print(f"  {'OK   ' if cond else 'FALLA'} {nombre}" + (f"\n         → {extra}" if not cond and extra else ""))
    if not cond:
        FALLOS.append(nombre)


def traza(**kw):
    base = {"id": 1, "account_id": 3, "ordenes_guardadas": 3, "error": None,
            "response_snippet": None, "post_url": "https://ig.com/p/x",
            "client_ig_username": "vipsportslv", "idventa": "32600",
            "user_id": 9, "username": "facu"}
    base.update(kw)
    return base


def resp(**kw):
    d = {"success": False, "insertadas": 0, "messages": [], "warnings": [], "errors": []}
    d.update(kw)
    return json.dumps(d)


print("== 1. El CRM rechazó la tanda entera: se puede remandar ==")
r, aviso = web._reintento_de_traza(traza(response_snippet=resp(insertadas=0)))
check("se ofrece reintentar", r is True)
check("sin aviso de duplicado, porque no entró nada", aviso is False)

print("\n== 2. El CRM insertó algo: NO se ofrece ==")
# La invariante que protege la plata.
r, aviso = web._reintento_de_traza(traza(response_snippet=resp(success=True, insertadas=2)))
check("no se ofrece el botón", r is False, "remandar duplicaría lo que ya entró")
r, _ = web._reintento_de_traza(traza(response_snippet=resp(insertadas=1)))
check("aunque haya entrado una sola", r is False)

print("\n== 3. Falló antes de mandar: seguro ==")
for err in ("GrowiAuthError: el CRM no abrió la sesión",
            "CredencialAusente: sesión sin contraseña",
            "CuentaSinCRM: no hay campaña",
            "ConnectTimeout: no conecta",
            "ProxyError: proxy caído"):
    r, aviso = web._reintento_de_traza(traza(error=err))
    check(f"{err.split(':')[0]:18} → reintentable sin aviso", r is True and aviso is False,
          f"r={r} aviso={aviso}")

print("\n== 4. No sabemos si salió: se ofrece, pero avisando ==")
r, aviso = web._reintento_de_traza(traza(error="ReadTimeout: se cortó esperando"))
check("se ofrece igual", r is True, "perder la orden por las dudas es peor")
check("pero con aviso de duplicado", aviso is True)

print("\n== 5. Sin órdenes guardadas no hay nada que mandar ==")
r, aviso = web._reintento_de_traza(traza(ordenes_guardadas=0, error="lo que sea"))
check("no se ofrece", r is False)

print("\n== 6. El payload truncado cuenta como sin órdenes ==")
# _acotar_payload reemplaza los payloads gigantes por un resumen: reenviar eso
# mandaría una orden incompleta.
from common import repository as repo                               # noqa: E402


class FilaFalsa:
    def __init__(self, payload):
        self.request_payload = payload
        for k in ("id", "trace_id", "origen", "operacion", "method", "url",
                  "account_id", "user_id", "username", "status_code",
                  "duracion_ms", "intentos", "proxy", "error", "post_url",
                  "client_ig_username", "idventa", "idvendedor", "costo"):
            setattr(self, k, None)
        self.ok = False
        self.response_body = None
        self.created_at = None


d = repo._growi_call_to_dict(FilaFalsa({"ordenes": [{"a": 1}, {"b": 2}]}))
check("cuenta las órdenes guardadas", d["ordenes_guardadas"] == 2, d)
check("y no la marca truncada", d["payload_truncado"] is False, d)

d = repo._growi_call_to_dict(FilaFalsa({"_truncado": True, "_bytes": 999999}))
check("un payload truncado no ofrece órdenes", d["ordenes_guardadas"] == 0, d)
check("y queda marcado como truncado", d["payload_truncado"] is True, d)

print("\n== 7. El snippet cortado no hace inventar un número ==")
# response_snippet son los primeros 300 caracteres: si el JSON no cierra, no se
# puede afirmar cuántas entraron, y ante la duda se avisa.
largo = '{"success":false,"insertadas":0,"messages":["' + "x" * 400
check("se rescata insertadas aunque el JSON no cierre",
      web._insertadas_de_traza({"response_snippet": largo}) == 0)
check("sin cuerpo, no se sabe (-1)",
      web._insertadas_de_traza({"response_snippet": None}) == -1)
check("cuerpo que no es JSON ni tiene el campo, no se sabe (-1)",
      web._insertadas_de_traza({"response_snippet": "<html>502</html>"}) == -1)
# Y ese "no se sabe" tiene que terminar en aviso, no en silencio.
r, aviso = web._reintento_de_traza(traza(response_snippet="<html>502</html>"))
check("un cuerpo ilegible se ofrece con aviso", r is True and aviso is True)

print()
if FALLOS:
    print(f"FALLARON {len(FALLOS)}:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("TODO OK — se puede remandar lo que rebotó, y nunca lo que ya entró")
