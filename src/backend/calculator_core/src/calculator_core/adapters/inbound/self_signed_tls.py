"""In-memory self-signed certificate for the TLS hop between the load balancer and a service.

The load balancer does not validate the certificate of its targets, so a throwaway
certificate is enough: ECDSA P-256, valid one day, generated at every start. The private key
only exists in memory and in a file readable by the owner alone; it is never logged.
"""

import datetime
import ipaddress
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALIDITY = datetime.timedelta(days=1)
_PRIVATE_FILE_MODE = 0o600


@dataclass(frozen=True)
class CertificateFiles:
    certificate: Path
    key: Path


def generate_certificate(common_name: str) -> tuple[bytes, bytes]:
    """Return the PEM certificate and the PEM private key (not encrypted, never logged)."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + VALIDITY)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(common_name), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


@contextmanager
def self_signed_files(common_name: str, parent: Path) -> Iterator[CertificateFiles]:
    """Write a fresh certificate and key in a private subdirectory of `parent`; remove it after."""
    certificate_pem, key_pem = generate_certificate(common_name)
    directory = Path(tempfile.mkdtemp(prefix="tls-", dir=parent))  # mode 0700
    try:
        files = CertificateFiles(directory / "tls.crt", directory / "tls.key")
        _write_private(files.certificate, certificate_pem)
        _write_private(files.key, key_pem)
        yield files
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
