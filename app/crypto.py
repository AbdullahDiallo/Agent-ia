import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

_key_cache: bytes | None = None

def _load_key() -> bytes:
    global _key_cache
    if _key_cache is not None:
        return _key_cache
    if not settings.app_encryption_key_base64:
        if not getattr(settings, "allow_ephemeral_encryption_key", False):
            raise RuntimeError("APP_ENCRYPTION_KEY_BASE64 must be set (or ALLOW_EPHEMERAL_ENCRYPTION_KEY=true for dev)")
        # For dev/test only, generate ephemeral key
        logger.warning("Using ephemeral encryption key; encrypted data will be lost on restart")
        key = AESGCM.generate_key(bit_length=256)
        _key_cache = key
        return key
    key = base64.b64decode(settings.app_encryption_key_base64)
    if len(key) not in (16, 24, 32):
        raise ValueError("invalid encryption key")
    _key_cache = key
    return key


def encrypt(plaintext: bytes, aad: bytes | None = None) -> bytes:
    key = _load_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext, aad)
    return nonce + ct


def decrypt(cipherblob: bytes, aad: bytes | None = None) -> bytes:
    key = _load_key()
    aesgcm = AESGCM(key)
    nonce, ct = cipherblob[:12], cipherblob[12:]
    return aesgcm.decrypt(nonce, ct, aad)
