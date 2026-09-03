from __future__ import annotations

import ipaddress
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from agora.remote.client import AgoraApiClient


def test_remote_client_refuses_plain_http() -> None:
    with pytest.raises(ValueError, match="https"):
        AgoraApiClient("http://localhost:8741", "not-transmitted")


def test_incorrect_ca_is_rejected_by_real_tls_handshake(tmp_path: Path) -> None:
    trusted_ca, server_cert, server_key = _certificate_chain(tmp_path, "trusted")
    wrong_ca, _, _ = _certificate_chain(tmp_path, "wrong")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(server_cert, server_key)
    wrong_client = ssl.create_default_context(cafile=wrong_ca)
    correct_client = ssl.create_default_context(cafile=trusted_ca)

    with pytest.raises(ssl.SSLCertVerificationError):
        _memory_handshake(wrong_client, server_context)

    _memory_handshake(correct_client, server_context)


def _certificate_chain(root: Path, stem: str) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Agora {stem} test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = root / f"{stem}-ca.pem"
    cert_path = root / f"{stem}-server.pem"
    key_path = root / f"{stem}-server-key.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(ca_path), str(cert_path), str(key_path)


def _memory_handshake(client_context: ssl.SSLContext, server_context: ssl.SSLContext) -> None:
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_context.wrap_bio(
        client_in,
        client_out,
        server_side=False,
        server_hostname="localhost",
    )
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    client_done = server_done = False
    for _ in range(20):
        if not client_done:
            try:
                client.do_handshake()
                client_done = True
            except ssl.SSLWantReadError:
                pass
        _transfer(client_out, server_in)
        if not server_done:
            try:
                server.do_handshake()
                server_done = True
            except ssl.SSLWantReadError:
                pass
        _transfer(server_out, client_in)
        if client_done and server_done:
            return
    raise AssertionError("TLS handshake did not complete")


def _transfer(source: ssl.MemoryBIO, destination: ssl.MemoryBIO) -> None:
    payload = source.read()
    if payload:
        destination.write(payload)
