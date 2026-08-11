"""
Cifrado simétrico para datos que salen del proceso. Hoy queda un solo uso: la
cookie que recuerda el usuario para precargar el login. Usamos Fernet (AES-128 +
HMAC) con una clave tomada de DB_ENCRYPTION_KEY.

Ojo: esto NO es para contraseñas. La del CRM de Growi no se guarda en ningún
lado —ni cifrada— porque la clave vive en el mismo entorno que los datos, así
que quien tiene una tiene la otra. Vive solo en memoria del webService mientras
el vendedor está logueado.

Generar la clave una vez con:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
y ponerla en DB_ENCRYPTION_KEY (mismo valor en ambos servicios).
"""
import os
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    key = os.environ.get("DB_ENCRYPTION_KEY", "").strip()
    if not key:
        # Fallback derivado del SECRET_KEY para entornos sin clave dedicada.
        # En producción conviene setear DB_ENCRYPTION_KEY explícita.
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
