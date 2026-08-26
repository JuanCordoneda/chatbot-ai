"""El envío rebotado por el x-growi-token se rescata solo.

El agujero, con fecha: hasta el 25/8/26 el CRM rechazaba por token vencido en
silencio (success:false, insertadas:0, y ni errors ni warnings ni messages), y
`_rebote_por_token` lo reconocía por esa mudez. Ese día el CRM empezó a explicar
el rechazo —"esta pestaña perdió el permiso de escritura…", con
`token_caducado: true`— así que `errors` dejó de venir vacío, la firma no
enganchó más, y a partir de ahí TODOS los envíos con el token vencido murieron
al primer tiro. Dos días de órdenes rebotadas.

De yapa, el texto del CRM dice "la sesión se renovó sola": eso matcheaba la
aguja "sesión" y la pantalla del vendedor lo mostraba como "El CRM cortó la
sesión", mandando a revisar un login que estaba perfecto.

    PYTHONPATH=.:webService python3 scripts/tests/test_rebote_token.py
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


# El cuerpo REAL que contestó el CRM (traza 13ae147098d1…, 26/8/26 22:18).
CUERPO_TOKEN = {
    "success": False, "insertadas": 0, "messages": [], "warnings": [],
    "errors": ["Esta pestaña perdió el permiso de escritura (quedó abierta "
               "demasiado tiempo o la sesión se renovó sola). No se cargó nada. "
               "Recargá la página y volvé a enviar: no vas a perder las órdenes."],
    "meta": {"request_id": "fec0afeb433c", "ts": "2026-08-26T17:18:11-03:00"},
    "tech": None, "token_caducado": True,
}
# La firma vieja, la que sí reconocíamos: el mismo rechazo sin explicación.
CUERPO_MUDO = {"success": False, "insertadas": 0, "messages": [], "warnings": [],
               "errors": []}
CUERPO_OK = {"success": True, "insertadas": 20, "messages": [], "warnings": [],
             "errors": []}


class Resp:
    def __init__(self, cuerpo, status=200, url="https://crm.test/paginas/enviar_trafico.php"):
        self.status_code = status
        self.url = url
        self.text = json.dumps(cuerpo) if not isinstance(cuerpo, str) else cuerpo
        self.headers = {"content-type": "application/json"}
        self.request = None

    def json(self):
        return json.loads(self.text)


print("\n1) Reconocer el rebote por token")

check("el cuerpo real del CRM (token_caducado) engancha",
      web._rebote_por_token(Resp(CUERPO_TOKEN)) is True)
check("la firma vieja (rechazo mudo) sigue enganchando",
      web._rebote_por_token(Resp(CUERPO_MUDO)) is True)
check("un envío que entró no engancha",
      web._rebote_por_token(Resp(CUERPO_OK)) is False)
# El seguro que importa: reintentar algo que YA insertó se lo cobra dos veces al
# cliente. Aunque el CRM diga token_caducado, si insertó no se toca.
check("token_caducado con insertadas>0 NO se reintenta",
      web._rebote_por_token(Resp({**CUERPO_TOKEN, "insertadas": 3})) is False)
check("un rechazo legítimo (saldo) no engancha",
      web._rebote_por_token(Resp({"success": False, "insertadas": 0,
                                  "errors": ["Saldo insuficiente"]})) is False)
check("una página de HTML no engancha",
      web._rebote_por_token(Resp("<html>login</html>")) is False)


print("\n2) El envío se reintenta solo")


class SesionFake:
    """Contesta lo que le pongan en `respuestas`, una por POST."""

    def __init__(self, respuestas, token="tok-1"):
        self.respuestas = list(respuestas)
        self.posts = 0
        self.tokens_pedidos = 0
        self.token = token

    def request(self, method, url, **kw):
        if url.endswith("enviar_trafico.php"):
            self.posts += 1
            return self.respuestas.pop(0)
        return Resp({}, url=url)

    def get(self, url, **kw):
        # La lectura del <meta growi-token> desde trafico.php.
        self.tokens_pedidos += 1
        r = Resp("", url=url)
        r.text = f'<meta name="growi-token" content="{self.token}-{self.tokens_pedidos}">'
        return r


class RepoFake:
    RepoError = RuntimeError

    @staticmethod
    def get_account_crm_config(aid):
        return {"crm_email": "facu@growi.com", "crm_url": "https://crm.test",
                "crm_idvendedor": "77", "crm_idventa": "88"}


web._repo = RepoFake
web._credencial_de_sesion = lambda aid: "pw"     # sin request context

LOGINS = []


def montar(respuestas):
    """Deja una sesión ya abierta y devuelve el fake, para poder contar posts."""
    web._growi_sessions.clear()
    LOGINS.clear()
    fake = SesionFake(respuestas)
    cfg = web._account_crm_cfg(3)
    web._growi_sessions[web._clave_sesion(cfg)] = {"session": fake, "cfg": cfg,
                                                   "token": "viejo"}

    def _login_fake(cfg, verify=True, account_id=None):
        LOGINS.append(account_id)
        return fake
    web._growi_login_with = _login_fake
    return fake


def enviar():
    return web._growi_request("POST", "/paginas/enviar_trafico.php", account_id=3,
                              json={"ordenes": []}, headers={"referer": "x"})


fake = montar([Resp(CUERPO_TOKEN), Resp(CUERPO_OK)])
r = enviar()
check("con token_caducado se manda de nuevo", fake.posts == 2, f"posts={fake.posts}")
check("y el segundo POST va con un token nuevo", fake.tokens_pedidos >= 1)
check("la respuesta que vuelve es la que entró", r.json()["insertadas"] == 20)
check("el primer rescate NO relogueó (es el caro)", LOGINS == [], LOGINS)

# El CRM dice "la sesión se renovó sola": si es eso, el token nuevo sale de una
# sesión que ya no es la nuestra y vuelve a rebotar. Ahí sí hay que reloguear.
fake = montar([Resp(CUERPO_TOKEN), Resp(CUERPO_TOKEN), Resp(CUERPO_OK)])
r = enviar()
check("si el token nuevo tampoco sirve, reloguea y reintenta",
      fake.posts == 3 and LOGINS == [3], f"posts={fake.posts}, logins={LOGINS}")
check("y termina entrando", r.json()["insertadas"] == 20)

# Sin techo, un CRM que rebota siempre nos deja martillándolo.
fake = montar([Resp(CUERPO_TOKEN)] * 4)
r = enviar()
check("con el CRM rebotando siempre, se corta en 3 POSTs",
      fake.posts == 3, f"posts={fake.posts}")
check("y devuelve el rechazo del CRM, no una excepción",
      r.json().get("token_caducado") is True)

fake = montar([Resp({**CUERPO_TOKEN, "insertadas": 3})])
r = enviar()
check("si el CRM llegó a insertar, NO se reintenta", fake.posts == 1, f"posts={fake.posts}")


print("\n3) Lo que ve el vendedor")

traza = {"error": None, "status_code": 200,
         "response_snippet": json.dumps(CUERPO_TOKEN)[:300]}

codigo, criollo = web._motivo_envio_criollo(traza["error"], traza["response_snippet"])
check("el rebote por token no se disfraza de sesión caída", codigo == "token",
      f"{codigo}: {criollo}")

# "401" pelado matcheaba cualquier número que lo tuviera adentro.
codigo, _ = web._motivo_envio_criollo(
    None, '{"success":false,"errors":["La campaña 33401 está cerrada"]}')
check("una campaña 33401 no es una sesión caída", codigo == "rechazado", codigo)
codigo, _ = web._motivo_envio_criollo("HTTPError: 401 Client Error: Unauthorized")
check("un 401 de verdad sí lo es", codigo == "sesion", codigo)

tecnico = web._tecnico_de_traza(traza)
check("el detalle técnico deja de ser un 'HTTP 200' pelado",
      "permiso de escritura" in tecnico, tecnico)
check("y sigue diciendo el status", tecnico.startswith("HTTP 200"), tecnico)
check("un fallo con error propio no cambia",
      web._tecnico_de_traza({"error": "GrowiAuthError: x", "status_code": None})
      == "GrowiAuthError: x")
# El snippet son 300 caracteres: si el JSON viene más largo no parsea y hay que
# rescatar el mensaje igual.
check("con el JSON cortado por el snippet, igual saca el mensaje",
      "perdió el permiso" in web._tecnico_de_traza(
          {"error": None, "status_code": 200,
           "response_snippet": '{"success":false,"errors":["perdió el permiso de '
                               'escritura y el resto del json quedó cortado por el'}))

print(f"\n{'TODO OK' if not FALLOS else 'FALLAN: ' + ', '.join(FALLOS)}")
sys.exit(1 if FALLOS else 0)
