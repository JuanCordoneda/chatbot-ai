"""
Catálogo de los errores que puede ver un vendedor al mandar una orden.

Cada bloque provoca una falla distinta y afirma dos cosas:
  - que el mensaje que llega a la pantalla EXPLICA qué pasó y qué hacer;
  - que NO se filtra infraestructura (IPs, puertos, hosts internos, nombres de
    excepción, tracebacks), que es lo que se veía antes cuando el backend
    devolvía str(e) tal cual.

Al final imprime el catálogo completo, para poder leerlo de un vistazo.
"""
import json
import os
import sys

sys.path.insert(0, "/app")

import requests as _rq                                              # noqa: E402


def _prohibido(*a, **kw):
    raise AssertionError(f"SALIDA A LA RED REAL BLOQUEADA: {a[:2]}")


_rq.Session.request = _prohibido
_rq.request = _prohibido

import app as web                                                   # noqa: E402

FALLOS = []
CATALOGO = []


def check(nombre, cond, detalle=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" +
          (f"\n         → {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLOS.append(nombre)


# Palabras que NUNCA pueden llegar a la pantalla del vendedor.
FUGAS = ["/paginas/", "Traceback", "ProxyError", "ConnectTimeout", "ReadTimeout", "HTTPSConnectionPool",
         "8888", "railway.internal", "127.0.0.1", "0.0.0.0", "psycopg2", "sqlalchemy",
         "openai-service", "web-service", "Exception", "None", "object at 0x"]


def sin_fugas(texto):
    t = texto or ""
    return [f for f in FUGAS if f in t]


class FakeResp:
    def __init__(self, payload=None, texto=None, status=200, url="https://crm.fake/x"):
        self._payload, self.status_code = payload, status
        self.text = texto if texto is not None else json.dumps(payload or {})
        self.url, self.headers = url, {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload

    def raise_for_status(self):
        pass


def base_ok(method, path, account_id=None, **kw):
    if "server_time_ar" in path:
        return FakeResp({"ymdhmAR": "2026-08-10 11:00"})
    if "obtenercostotrafico" in path:
        return FakeResp({"costoTrafico": 0.715})
    if "enviar_trafico" in path:
        return FakeResp({"success": True, "insertadas": 1, "messages": ["ok"],
                         "warnings": [], "errors": []})
    raise AssertionError(path)


web._growi_request = base_ok
web._log_uso = lambda *a, **kw: None
web._cuenta_tiene_crm_propio = lambda aid: True
web._account_crm_cfg = lambda aid: {"crm_url": "https://crm.fake", "crm_email": "v@d",
                                    "crm_password": "x", "crm_disponible": "150"}
web.resolver_venta = lambda a, i, idventa_elegida=None, refrescar=False: {
    "idventa": "8811", "idvendedor": "1219", "origen": "auto",
    "detalle": "campaña", "saldo": 167.119}

cli = web.app.test_client()
with cli.session_transaction() as s:
    s["logged_in"] = True
    s["stamp"] = web.SESSION_STAMP     # si no, la sesión se invalida y da 401
    s["account_id"] = 7
    s["user_id"] = 21
    s["username"] = "tmignola"
    s["cred_key"] = web._guardar_credencial(7, "x")

ORDEN_T = {"redsocial_id": "1", "prod": "Followers", "url": "u", "costo": 0.715,
           "cant_inicial": "500", "cantidad": "500", "programado": 0,
           "fecha_programada": None, "comentarios": [], "producto_id": "1"}
COMS = ["mujeres:", "linda", "hombres:", "capo"]
ORDEN_C = {"tipo": "comentarios", "redsocialId": "1", "productoId": "94",
           "productoNombre": "Comentarios Reales Verificados", "link": "u",
           "costo": 0, "cantidad": 2, "cuando": "ahora", "comentarios": COMS}


def mandar_trafico():
    return cli.post("/api/enviar_trafico", json={"ordenes": [dict(ORDEN_T)], "url": "u",
                                                 "client": "lucydoughty"})


def mandar_comentarios():
    return cli.post("/api/publicar", json={"url": "u", "comentarios": COMS,
                                           "ordenes": [dict(ORDEN_C)],
                                           "client": "lucydoughty"})


def texto_de(r):
    d = r.get_json() or {}
    partes = [d.get("error") or ""]
    partes += ((d.get("resultado") or {}).get("errors") or [])
    return " | ".join(p for p in partes if p)


def caso(titulo, pantalla, fn, espera_en_pantalla):
    """Provoca la falla, captura el mensaje y lo valida."""
    r = fn()
    msg = texto_de(r)
    CATALOGO.append((pantalla, titulo, msg))
    check(f"{titulo}: le dice al vendedor qué pasa",
          espera_en_pantalla.lower() in msg.lower(), f"vio: {msg!r}")
    fugas = sin_fugas(msg)
    check(f"{titulo}: no filtra internals", not fugas, f"filtró {fugas} en {msg!r}")
    return r


print("\n== 1. El CRM no responde (proxy caído / sin red) ==")


def sin_red(method, path, account_id=None, **kw):
    if "enviar_trafico" in path:
        raise _rq.exceptions.ProxyError(
            "HTTPSConnectionPool(host='crm.growiagency.com', port=443): "
            "Max retries exceeded (ProxyError('Cannot connect to proxy', "
            "NewConnectionError('13.37.44.57:8888: Connection refused')))")
    return base_ok(method, path, account_id=account_id, **kw)


web._growi_request = sin_red
web._repo = None                      # sin DB no hay cola: se informa el error
caso("tráfico sin conexión", "Resultado", mandar_trafico, "problema de conexión")
caso("comentarios sin conexión", "Resultado", mandar_comentarios, "problema de conexión")

print("\n== 2. Se cortó esperando la respuesta (pudo haber entrado) ==")


def corta_leyendo(method, path, account_id=None, **kw):
    if "enviar_trafico" in path:
        raise _rq.exceptions.ReadTimeout("Read timed out. (read timeout=60)")
    return base_ok(method, path, account_id=account_id, **kw)


web._growi_request = corta_leyendo
caso("tráfico con corte de lectura", "Resultado", mandar_trafico, "revisá en growi")
caso("comentarios con corte de lectura", "Resultado", mandar_comentarios, "revisá en growi")

print("\n== 3. La cuenta no tiene su CRM configurado ==")
web._growi_request = base_ok
web._cuenta_tiene_crm_propio = lambda aid: False
caso("cuenta sin CRM (tráfico)", "Resultado", mandar_trafico, "no tiene el crm")
caso("cuenta sin CRM (comentarios)", "Resultado", mandar_comentarios, "no tiene el crm")
web._cuenta_tiene_crm_propio = lambda aid: True

print("\n== 4. No se pudo determinar la campaña / el idvendedor ==")
web.resolver_venta = lambda a, i, idventa_elegida=None, refrescar=False: {
    "idventa": "", "idvendedor": "", "origen": "default", "detalle": "", "saldo": None}
web._idvendedor_del_crm = lambda aid: ""
caso("sin campaña resuelta", "Resultado", mandar_trafico, "no pudimos determinar")
web.resolver_venta = lambda a, i, idventa_elegida=None, refrescar=False: {
    "idventa": "8811", "idvendedor": "1219", "origen": "auto",
    "detalle": "campaña", "saldo": 167.119}

print("\n== 5. El CRM rechaza el login del vendedor ==")


def login_rebota(method, path, account_id=None, **kw):
    if "enviar_trafico" in path:
        raise web.GrowiAuthError(
            "El CRM rebotó al login en /paginas/enviar_trafico.php después de 4 "
            "intentos. La sesión de Growi no se está abriendo: revisá las "
            "credenciales del vendedor y el proxy.")
    return base_ok(method, path, account_id=account_id, **kw)


web._growi_request = login_rebota
caso("credenciales del CRM rechazadas", "Resultado", mandar_trafico, "volver a entrar")

print("\n== 6. El CRM contesta pero rechaza la orden ==")


def rechaza(method, path, account_id=None, **kw):
    if "enviar_trafico" in path:
        return FakeResp({"success": False, "insertadas": 0, "messages": [],
                         "warnings": [], "errors": ["Saldo insuficiente en la campaña"]})
    return base_ok(method, path, account_id=account_id, **kw)


web._growi_request = rechaza
r = mandar_comentarios()
msg = texto_de(r)
CATALOGO.append(("Resultado", "el CRM rechaza la orden", msg))
check("el motivo del CRM llega tal cual", "Saldo insuficiente" in msg, msg)

print("\n== 7. El CRM contesta cualquier cosa (HTML en vez de JSON) ==")


def html(method, path, account_id=None, **kw):
    if "enviar_trafico" in path:
        return FakeResp(None, texto="<html><body>502 Bad Gateway</body></html>")
    return base_ok(method, path, account_id=account_id, **kw)


web._growi_request = html
r = mandar_trafico()
msg = texto_de(r)
CATALOGO.append(("Resultado", "el CRM devuelve HTML", msg))
check("se avisa sin volcar el HTML", bool(msg) and "<html>" not in msg, msg)
check("no filtra internals", not sin_fugas(msg), msg)

print("\n== 8. La orden queda EN COLA (no es un fallo) ==")


class RepoCola:
    @staticmethod
    def encolar_orden(post_url, payload, **kw):
        return {"id": 77}

    @staticmethod
    def get_account_crm_config(aid):
        return {"crm_email": "v@d"}


web._repo = RepoCola
web._growi_request = sin_red
r = mandar_comentarios()
res = (r.get_json() or {}).get("resultado") or {}
CATALOGO.append(("Resultado", "orden encolada", (res.get("errors") or [""])[0]))
check("el backend marca encolada=True", res.get("encolada") is True, res)
check("con el id de la cola", res.get("encolada_id") == 77, res)
check("y el mensaje dice cómo reintentarla",
      "Reintentala" in (res.get("errors") or [""])[0], res.get("errors"))

# Ruta del contenedor con fallback al repo, para poder correrlo también local.
_js_path = "/app/static/app.js"
if not os.path.exists(_js_path):
    _js_path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "webService", "static", "app.js")
js = open(_js_path).read()
check("la pantalla la muestra como guardada, no en rojo",
      "Guardada — reintentala" in js and "const encolada = !!(rc && rc.encolada)" in js)
check("y no la cuenta como fallo",
      "const fallo = errores.length > 0 && !encolada;" in js)

print("\n== 9. Faltan datos / no hay órdenes ==")
web._growi_request = base_ok
web._repo = None
r = cli.post("/api/publicar", json={"url": "", "comentarios": [], "ordenes": []})
CATALOGO.append(("Órdenes", "publicar sin datos", (r.get_json() or {}).get("error")))
check("publicar sin datos avisa", r.status_code == 400 and (r.get_json() or {}).get("error"))
r = cli.post("/api/enviar_trafico", json={"ordenes": []})
CATALOGO.append(("Órdenes", "enviar sin órdenes", (r.get_json() or {}).get("error")))
check("tráfico sin órdenes avisa", r.status_code == 400 and (r.get_json() or {}).get("error"))

print("\n== 10. Sesión vencida del panel ==")
anon = web.app.test_client()
r = anon.post("/api/enviar_trafico", json={"ordenes": [dict(ORDEN_T)]})
CATALOGO.append(("Cualquiera", "sesión del panel vencida", (r.get_json() or {}).get("error")))
check("responde 401 y no ejecuta nada", r.status_code == 401, r.status_code)

print("\n" + "=" * 78)
print("CATÁLOGO DE MENSAJES QUE VE EL VENDEDOR")
print("=" * 78)
for pantalla, titulo, msg in CATALOGO:
    print(f"\n[{pantalla}] {titulo}")
    print(f"   \"{msg}\"")

print("\n" + ("TODO OK" if not FALLOS else f"FALLARON {len(FALLOS)}:\n  - " + "\n  - ".join(FALLOS)))
sys.exit(1 if FALLOS else 0)
