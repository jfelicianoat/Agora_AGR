"""CA privada para el TLS interno de Agora: emisión, renovación y anclaje.

F2 exigía «bootstrap controlado, renovación y almacenamiento seguro de claves»
y no había nada: `agora-api` pedía `--cert`/`--key` sin decir de dónde salían.
Esto lo cubre sin depender de una PKI externa, que es lo que corresponde a una
red doméstica con dos máquinas.

Reparto de secretos, deliberado:

- **La clave de la CA se guarda cifrada.** Es la raíz de confianza y vive
  años; comprometerla permite suplantar el tablero. La frase de paso llega por
  entorno, nunca por línea de órdenes (que es visible en el gestor de tareas).
- **La clave del servidor se guarda en claro.** La lee `uvicorn` al arrancar sin
  nadie delante, y es reemplazable en un minuto con `renew`. Cifrarla sólo
  movería el secreto a otro fichero del mismo disco.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
from base64 import b64encode
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_PASSPHRASE_ENV = "AGORA_CA_PASSPHRASE"
CA_CERTIFICATE = "ca.crt"
CA_KEY = "ca.key"
SERVER_CERTIFICATE = "server.crt"
SERVER_KEY = "server.key"

# Los emisores públicos ya no firman por más de 398 días; un plazo corto obliga
# a que la renovación esté probada en vez de ser teórica.
DEFAULT_SERVER_DAYS = 397
DEFAULT_CA_DAYS = 3650


class CertificateError(RuntimeError):
    """Error de configuración de la PKI interna, no un fallo criptográfico."""


def _passphrase() -> bytes:
    value = os.environ.get(CA_PASSPHRASE_ENV, "")
    if len(value) < 12:
        raise CertificateError(
            f"set {CA_PASSPHRASE_ENV} to at least 12 characters; "
            "the CA private key is never written unencrypted"
        )
    return value.encode("utf-8")


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    # En Windows el permiso efectivo lo pone la ACL del directorio; el modo
    # POSIX se aplica igual para que el fichero no viaje legible a otro sistema.
    with suppress(OSError):
        path.chmod(0o600)


def _san(hosts: tuple[str, ...]) -> x509.SubjectAlternativeName:
    if not hosts:
        raise CertificateError("a server certificate needs at least one host")
    entries: list[x509.GeneralName] = []
    for host in hosts:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            entries.append(x509.DNSName(host))
    return x509.SubjectAlternativeName(entries)


def spki_sha256(certificate: x509.Certificate) -> str:
    """Anclaje estable: la clave pública sobrevive a una renovación del certificado."""
    public = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return b64encode(hashlib.sha256(public).digest()).decode("ascii")


@dataclass(frozen=True, slots=True)
class CertificateAuthority:
    directory: Path

    @property
    def certificate_path(self) -> Path:
        return self.directory / CA_CERTIFICATE

    @property
    def key_path(self) -> Path:
        return self.directory / CA_KEY

    def exists(self) -> bool:
        return self.certificate_path.is_file() and self.key_path.is_file()

    def create(
        self,
        *,
        common_name: str = "Agora internal CA",
        days: int = DEFAULT_CA_DAYS,
    ) -> Path:
        if self.exists():
            raise CertificateError(
                f"{self.directory} already holds a CA; issuing a second one would "
                "silently split the trust anchor"
            )
        key = ec.generate_private_key(ec.SECP384R1())
        now = dt.datetime.now(dt.UTC)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .sign(key, hashes.SHA384())
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        _write_private(
            self.key_path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(_passphrase()),
            ),
        )
        return self.certificate_path

    def load(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        if not self.exists():
            raise CertificateError(f"no CA in {self.directory}; run `agora-certs init-ca` first")
        certificate = x509.load_pem_x509_certificate(self.certificate_path.read_bytes())
        try:
            key = serialization.load_pem_private_key(
                self.key_path.read_bytes(), password=_passphrase()
            )
        except ValueError as exc:
            raise CertificateError(
                f"could not decrypt the CA key; is {CA_PASSPHRASE_ENV} the right passphrase?"
            ) from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise CertificateError("the CA key is not an EC key issued by agora-certs")
        return certificate, key

    def issue(
        self,
        hosts: tuple[str, ...],
        destination: Path,
        *,
        days: int = DEFAULT_SERVER_DAYS,
        reuse_key: bool = False,
    ) -> tuple[Path, Path]:
        ca_certificate, ca_key = self.load()
        certificate_path = destination / SERVER_CERTIFICATE
        key_path = destination / SERVER_KEY
        if reuse_key:
            if not key_path.is_file():
                raise CertificateError(f"no existing key at {key_path} to reuse")
            existing = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(existing, ec.EllipticCurvePrivateKey):
                raise CertificateError("the server key is not an EC key issued by agora-certs")
            key = existing
        else:
            key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
            .issuer_name(ca_certificate.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=days))
            .add_extension(_san(hosts), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            # OpenSSL 3 rechaza con «Missing Authority Key Identifier» una cadena
            # sin este enlace, aunque la firma sea correcta. Comprobado con un
            # handshake real, no deducido del RFC.
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA384())
        )
        destination.mkdir(parents=True, exist_ok=True)
        certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        if not reuse_key:
            _write_private(
                key_path,
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            )
        return certificate_path, key_path


@dataclass(frozen=True, slots=True)
class CertificateDetails:
    """Lo que hace falta para decidir si hay que renovar, y para anclar."""

    subject: str
    issuer: str
    serial: str
    hosts: tuple[str, ...]
    not_before: str
    not_after: str
    days_remaining: int
    spki_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "serial": self.serial,
            "hosts": list(self.hosts),
            "not_before": self.not_before,
            "not_after": self.not_after,
            "days_remaining": self.days_remaining,
            "spki_sha256": self.spki_sha256,
        }


def describe(path: Path) -> CertificateDetails:
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        hosts = tuple(str(name.value) for name in san.value)
    except x509.ExtensionNotFound:
        hosts = ()
    expires = certificate.not_valid_after_utc
    return CertificateDetails(
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        serial=f"{certificate.serial_number:x}",
        hosts=hosts,
        not_before=certificate.not_valid_before_utc.isoformat(),
        not_after=expires.isoformat(),
        days_remaining=(expires - dt.datetime.now(dt.UTC)).days,
        spki_sha256=spki_sha256(certificate),
    )
