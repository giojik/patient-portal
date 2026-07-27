"""
აპლიკაციის დონის დაშიფვრა/გაშიფვრა მგრძნობიარე ველებისთვის.
გასაღები მოდის environment-იდან — არასდროს არ ინახება ბაზაში ან კოდში.
"""
import os
import hmac
import hashlib
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENC_KEY = base64.b64decode(os.environ["ENCRYPTION_KEY"])
_PEPPER = os.environ["HASH_PEPPER"].encode()

aesgcm = AESGCM(_ENC_KEY)


def encrypt_field(plaintext: str) -> str:
    """აბრუნებს base64(nonce + ciphertext)-ს."""
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_field(token: str) -> str:
    raw = base64.b64decode(token)
    nonce, ct = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ct, None).decode()


def hash_lookup(value: str) -> str:
    """HMAC-SHA256 საძიებო ინდექსისთვის (მაგ. login)."""
    return hmac.new(_PEPPER, value.encode(), hashlib.sha256).hexdigest()