"""Encrypts platform access tokens at rest (Fernet, key from settings)."""
import base64
import hashlib

from django.conf import settings


def _fernet():
    from cryptography.fernet import Fernet
    material = getattr(settings, 'CAPI_ENCRYPTION_KEY', '') or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ''


def decrypt(token: str) -> str:
    if not token:
        return ''
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # Key changed since the token was saved -> treat as missing
        return ''
