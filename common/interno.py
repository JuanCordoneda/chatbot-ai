"""
Autenticación entre nuestros propios servicios.

El openAIService tiene dominio público en Railway. Casi todo lo que expone es
inofensivo si alguien lo encuentra, pero la administración de las sesiones de
Instagram no lo es: quien pueda escribir ahí cambia con qué cuenta scrapeamos.
Estos endpoints piden una cabecera `X-Interno`.

El token sale de INTERNO_TOKEN si está seteada. Si no, se DERIVA de DATABASE_URL,
que ambos servicios ya comparten y nadie de afuera conoce. Eso es a propósito:
un esquema que necesita configurar una variable nueva en dos servicios se queda
sin configurar, y un endpoint "protegido" que en realidad quedó abierto es peor
que no tener el chequeo. Cuando se pueda, setear INTERNO_TOKEN explícita.
"""
import hashlib
import hmac
import os


def token() -> str:
    explicito = os.environ.get("INTERNO_TOKEN", "").strip()
    if explicito:
        return explicito
    semilla = os.environ.get("DATABASE_URL", "").strip()
    if not semilla:
        # Sin DB ni token no hay secreto compartido posible. Devolver "" hace
        # que verificar() rechace todo en vez de aceptar cualquier cosa.
        return ""
    return hmac.new(semilla.encode(), b"interno-v1", hashlib.sha256).hexdigest()


def verificar(recibido: str) -> bool:
    esperado = token()
    if not esperado or not recibido:
        return False
    return hmac.compare_digest(esperado, recibido.strip())
