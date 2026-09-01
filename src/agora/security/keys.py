"""RSA key helpers backed exclusively by cryptography and Authlib."""

from __future__ import annotations

from authlib.jose import JsonWebKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_private_key() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def public_key_pem(private_key: bytes) -> bytes:
    key = serialization.load_pem_private_key(private_key, password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def public_jwk(private_key: bytes) -> dict[str, str]:
    imported = JsonWebKey.import_key(public_key_pem(private_key)).as_dict()
    return {str(key): str(value) for key, value in imported.items()}

