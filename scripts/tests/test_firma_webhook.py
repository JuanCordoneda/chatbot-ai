"""
El webhook solo le hace caso a Meta.

`/whatsapp` tiene que estar abierto a internet para que Meta lo alcance, y
procesarlo dispara envíos. Sin verificar la firma, cualquiera que descubra la
URL puede hacer que el bot mande una tanda: gasta plata y le baja la calidad al
número, que es lo único que no se recupera comprando de nuevo.

Lo que se afirma acá:
  - con el secreto puesto, un cuerpo sin firma o con firma que no da se rechaza
    con 403 y NO se procesa;
  - la firma se valida contra los bytes exactos del cuerpo: cambiarle una coma
    al payload invalida la firma que venía con él;
  - sin secreto configurado se acepta igual, para no matar el webhook en
    silencio en una instalación que todavía no lo seteó;
  - el rechazo devuelve 403 y no 200: no es Meta, no hay reintento que cuidar
    (a Meta sí se le contesta 200 siempre, para que no deje de mandar).
"""
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "whatsappService"))

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido
_rq.post = _prohibido
_rq.get = _prohibido

import app as wa                                                    # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=""):
    print(f"{'OK  ' if cond else 'FALLA'}  {nombre}")
    if not cond:
        FALLOS.append(f"{nombre} {detalle}".strip())


# Nada de esto puede terminar mandando un mensaje: lo que se prueba es el portón,
# no lo que hay adentro.
wa.PEDIDO_ACTIVO = False
wa.OPENAI_SERVICE_URL = ""

SECRETO = "secreto-de-la-app"
CLIENTE = wa.app.test_client()

# Un webhook de verdad: un mensaje entrante de texto.
PAYLOAD = json.dumps({
    "entry": [{"changes": [{"value": {
        "messages": [{"from": "5492233407778", "id": "wamid.1",
                      "text": {"body": "hola"}}]
    }}]}]
}).encode()


def _firmar(cuerpo, secreto=SECRETO):
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _postear(cuerpo, firma=None):
    headers = {"Content-Type": "application/json"}
    if firma is not None:
        headers["X-Hub-Signature-256"] = firma
    return CLIENTE.post("/whatsapp", data=cuerpo, headers=headers)


# ── Con el secreto puesto ────────────────────────────────────────────────────
wa.APP_SECRET = SECRETO

r = _postear(PAYLOAD, _firmar(PAYLOAD))
check("la firma correcta pasa", r.status_code == 200, r.status_code)

r = _postear(PAYLOAD)
check("sin header de firma se rechaza", r.status_code == 403, r.status_code)

r = _postear(PAYLOAD, "sha256=" + "0" * 64)
check("una firma que no da se rechaza", r.status_code == 403, r.status_code)

r = _postear(PAYLOAD, _firmar(PAYLOAD, "otro-secreto"))
check("firmado con otro secreto se rechaza", r.status_code == 403, r.status_code)

r = _postear(PAYLOAD, "abc123")
check("una firma sin el prefijo sha256= se rechaza", r.status_code == 403, r.status_code)

# El caso que hace que valga la pena firmar sobre el crudo y no sobre el JSON
# parseado: el atacante reusa una firma vieja con OTRO contenido.
manipulado = PAYLOAD.replace(b"hola", b"chau")
r = _postear(manipulado, _firmar(PAYLOAD))
check("un cuerpo manipulado invalida la firma que traía",
      r.status_code == 403, r.status_code)

# ── El rechazo no puede tocar nada ───────────────────────────────────────────
# Si un payload falso llegara a procesarse, esto lo delataría: el número quedaría
# con la ventana marcada como abierta sin que nadie haya escrito.
wa._ultimo_inbound.clear()
_postear(PAYLOAD, "sha256=" + "0" * 64)
check("el payload rechazado no se procesa", wa._ultimo_inbound == {},
      wa._ultimo_inbound)

# Y el aceptado sí, para que el check de arriba signifique algo.
_postear(PAYLOAD, _firmar(PAYLOAD))
check("el payload firmado sí se procesa", wa._ultimo_inbound != {})

# ── Sin secreto configurado ──────────────────────────────────────────────────
wa.APP_SECRET = ""
r = _postear(PAYLOAD)
check("sin secreto no se valida nada y el webhook sigue vivo",
      r.status_code == 200, r.status_code)

print()
if FALLOS:
    print(f"{len(FALLOS)} FALLA(S):")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("Todo en orden.")
