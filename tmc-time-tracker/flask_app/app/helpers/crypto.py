# flask_app/app/helpers/crypto.py
"""Symmetric encryption for secrets we must persist (e.g. OAuth refresh tokens).

Uses Fernet (AES-128-CBC + HMAC) keyed by TOKEN_ENCRYPTION_KEY. That key must be a
urlsafe-base64-encoded 32-byte key, e.g. generated once via:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and stored as an environment variable / app secret. Losing or rotating this key
invalidates every previously stored token (users simply need to log in again).
"""

import os
from cryptography.fernet import Fernet, InvalidToken


class TokenEncryptionNotConfigured(RuntimeError):
    """Raised when TOKEN_ENCRYPTION_KEY is missing but encryption/decryption was requested."""


def _get_fernet():
    key = os.getenv('TOKEN_ENCRYPTION_KEY')
    if not key:
        raise TokenEncryptionNotConfigured(
            'TOKEN_ENCRYPTION_KEY is not set. Generate one with '
            '`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` '
            'and set it as an environment variable.'
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_text(plaintext):
    """Encrypt a string, returning a urlsafe-base64 ciphertext string (or None)."""
    if plaintext is None:
        return None
    return _get_fernet().encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt_text(ciphertext):
    """Decrypt a ciphertext string produced by encrypt_text (or None)."""
    if ciphertext is None:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except InvalidToken as exc:
        raise TokenEncryptionNotConfigured(
            'Stored token could not be decrypted with the current TOKEN_ENCRYPTION_KEY '
            '(key rotated or value corrupted). The affected user must sign in again.'
        ) from exc
