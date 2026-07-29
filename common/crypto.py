"""
Cifrado simétrico para secretos guardados en la DB (hoy: la password del CRM de
cada cuenta). Usamos Fernet (AES-128 + HMAC) con una clave tomada de
DB_ENCRYPTION_KEY. Nunca guardamos la password del CRM en texto plano.

Generar la clave una vez con:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
y ponerla en DB_ENCRYPTION_KEY (mismo valor en ambos servicios).
"""
import os
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _is_production() -> bool:
    if os.environ.get("APP_ENV", "").strip().lower() in ("prod", "production"):
        return True
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))


def _get_fernet() -> Fernet:
    key = os.environ.get("DB_ENCRYPTION_KEY", "").strip()
    if not key:
        # En producción NO se cifra con una clave adivinable: cortamos. El fallback
        # derivado del SECRET_KEY es solo para dev/local sin clave dedicada.
        if _is_production():
            raise RuntimeError(
                "DB_ENCRYPTION_KEY no está seteada en producción. Sin ella las "
                "passwords del CRM se cifrarían con una clave pública. Generá una "
                "con `python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`.")
        seed = os.environ.get("SECRET_KEY", "growi-secret-2026")
        key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
    elif isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # Token cifrado con otra clave (rotación mal hecha) o dato corrupto.
        return ""
