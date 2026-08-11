"""El detalle de un login fallido no inventa la causa.

`_detalle_login` es lo que ve el admin cuando a un vendedor no le abre la sesión
del CRM, y es la base del diagnóstico. Tiene que distinguir TRES situaciones:

  1. el CRM aceptó el login pero la sesión no quedó (IP / sesiones pisándose),
  2. el CRM rebotó al login (credenciales),
  3. no hay señal: el POST no redirigió a ningún lado.

El caso 3 caía en el mismo texto que el 2 y mandaba a revisar la contraseña sin
tener con qué afirmar que fuera esa. Con otro CRM —o si Growi cambia su
formulario— ese texto manda a cambiar una contraseña que está bien.

    PYTHONPATH=.:webService python3 scripts/tests/test_detalle_login.py
"""
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


class RespFalsa:
    """Lo mínimo de una respuesta de requests que miran _detalle_login y
    _huella_respuesta."""

    def __init__(self, status=200, location=None, url="https://crm.test/cuenta/login.php",
                 body="<html><body><form>Usuario o contraseña incorrectos</form></body></html>",
                 tipo="text/html; charset=utf-8", cookies=()):
        self.status_code = status
        self.url = url
        self.history = [_Redirect(302, location)] if location else []
        self.text = body
        self.headers = {"content-type": tipo}
        self.cookies = {c: "x" for c in cookies}


class _Redirect:
    def __init__(self, status, location):
        self.status_code = status
        self.headers = {"location": location}


CFG = {"crm_email": "facu@growi.com", "crm_url": "https://crm.test"}
AL_LOGIN = {"ok": False, "status": 302, "location": "/cuenta/login.php"}


print("== 1. El CRM aceptó el login pero la sesión no quedó ==")
# Redirect a una página interna: las credenciales sirven.
d = web._detalle_login(CFG, RespFalsa(location="/paginas/trafico.php"), AL_LOGIN)
check("dice que NO es la contraseña", "No es la contraseña" in d, d)
check("nombra las causas reales (IP / sesiones pisándose)",
      "IP de salida" in d and "pisándose" in d, d)

print("\n== 2. El CRM rebotó al login ==")
# Redirect de vuelta a login.php: rechazo explícito.
d = web._detalle_login(CFG, RespFalsa(location="/cuenta/login.php"), AL_LOGIN)
check("apunta a la contraseña", "contraseña" in d, d)
check("y lo dice como probable, no como certeza", "Casi seguro" in d, d)
check("no manda a la ficha del vendedor (ahí ya no hay contraseña)",
      "ficha del vendedor" not in d, d)

print("\n== 3. Sin redirect: no hay señal, no se afirma nada ==")
# Este es el caso del bug: 200 pelado, sin history.
d = web._detalle_login(CFG, RespFalsa(status=200), AL_LOGIN)
check("no afirma que sea la contraseña", "Casi seguro" not in d, d)
check("ofrece las DOS posibilidades", "Puede ser la contraseña" in d and "formulario de login" in d, d)
check("dice cómo distinguirlas", "entrar a mano" in d, d)

print("\n== 4. En los tres casos van los datos crudos ==")
for nombre, resp in (("aceptado", RespFalsa(location="/paginas/trafico.php")),
                     ("rebotado", RespFalsa(location="/cuenta/login.php")),
                     ("sin señal", RespFalsa(status=200))):
    d = web._detalle_login(CFG, resp, AL_LOGIN)
    check(f"[{nombre}] trae el login.php y el trafico.php",
          "login.php →" in d and "trafico.php →" in d, d)
    check(f"[{nombre}] nombra al vendedor", CFG["crm_email"] in d, d)

print("\n== 4b. La huella de la respuesta es lo que desempata ==")
# El caso real de producción: 200 sin redirect. Lo único que distingue "me
# rechazó la contraseña" de "me está limitando" es el texto del cuerpo.
d = web._detalle_login(CFG, RespFalsa(status=200), AL_LOGIN)
check("el detalle incluye lo que contestó el CRM", "Respuesta del CRM:" in d, d)
check("con el texto visible del cuerpo", "Usuario o contraseña incorrectos" in d, d)

limitado = RespFalsa(status=200, body="<html>Demasiados intentos. Esperá 15 minutos.</html>")
d = web._detalle_login(CFG, limitado, AL_LOGIN)
check("si el CRM dice que nos limita, se lee en el detalle",
      "Demasiados intentos" in d, d)

# El HTML no se vuelca entero: solo el texto visible y recortado.
ruidosa = RespFalsa(status=200, body="<script>var secreto='no va'</script><p>Hola</p>" + "x" * 5000)
h = web._huella_respuesta(ruidosa)
check("no vuelca el script", "secreto" not in h, h)
check("y recorta el cuerpo largo", len(h) < 500, len(h))
check("pero deja el tamaño real para poder comparar", "5047b" in h or "b;" in h, h)

check("sin respuesta no explota", web._huella_respuesta(None) == "sin respuesta")

print("\n== 4c. Cuántos logins veníamos haciendo ==")
web._LOGINS_RECIENTES.clear()
for _ in range(5):
    n = web._anotar_login(("https://crm.test", "facu@growi.com"))
check("cuenta los logins de esa credencial", n == 5, n)
check("no mezcla credenciales distintas",
      web._anotar_login(("https://crm.test", "otro@growi.com")) == 1)
d = web._detalle_login(CFG, RespFalsa(status=200), AL_LOGIN, logins_recientes=n)
check("el detalle dice cuántos logins hubo", "5 login/s" in d, d)
d = web._detalle_login(CFG, RespFalsa(status=200), AL_LOGIN)
check("y sin el dato no inventa un número", "login/s" not in d, d)

print("\n== 5. Sin respuesta de login (ni se pudo postear) ==")
d = web._detalle_login(CFG, None, AL_LOGIN)
check("no explota y sigue sin inventar causa", "Puede ser la contraseña" in d, d)

print("\n== 6. La señal se lee del redirect, no del status final ==")
# El POST sigue redirects: un 200 final con history a login.php es un RECHAZO,
# no una falta de señal. Si se mirara solo el status, se leería al revés.
check("un 200 final con redirect a login.php se lee como rechazo",
      web._login_parece_aceptado(RespFalsa(status=200, location="/cuenta/login.php")) is False)
check("un 200 final con redirect a una interna se lee como aceptado",
      web._login_parece_aceptado(RespFalsa(status=200, location="/paginas/trafico.php")) is True)
check("un 200 sin redirect no da señal (None, no False)",
      web._login_parece_aceptado(RespFalsa(status=200)) is None)

print()
if FALLOS:
    print(f"FALLARON {len(FALLOS)}:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
print("TODO OK — el detalle del login describe lo que pasó sin inventar la causa")
