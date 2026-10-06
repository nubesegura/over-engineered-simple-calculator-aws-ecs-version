import datetime
import http.client
import os
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from calculator_core.adapters.inbound.self_signed_tls import generate_certificate, self_signed_files


def test_certificate_is_ecdsa_p256_valid_one_day_for_the_service_name() -> None:
    pem, key_pem = generate_certificate("calc-add-dev")

    certificate = x509.load_pem_x509_certificate(pem)
    public_key = certificate.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    assert public_key.curve.name == "secp256r1"
    assert certificate.subject.rfc4514_string() == "CN=calc-add-dev"
    lifetime = certificate.not_valid_after_utc - certificate.not_valid_before_utc
    assert lifetime <= datetime.timedelta(days=1, minutes=10)
    assert certificate.not_valid_after_utc > datetime.datetime.now(datetime.UTC)
    assert b"PRIVATE KEY" in key_pem


def test_files_are_private_and_removed_afterwards(tmp_path: Path) -> None:
    with self_signed_files("calc-add-dev", tmp_path) as files:
        assert files.certificate.read_bytes().startswith(b"-----BEGIN CERTIFICATE")
        assert files.key.read_bytes().startswith(b"-----BEGIN PRIVATE KEY")
        if os.name == "posix":
            assert files.key.stat().st_mode & 0o777 == 0o600
            assert files.key.parent.stat().st_mode & 0o777 == 0o700
        directory = files.key.parent

    assert not directory.exists()


@pytest.fixture
def https_server(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    async def health(request: object) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with self_signed_files("calc-add-dev", tmp_path) as files:
        config = uvicorn.Config(
            Starlette(routes=[Route("/health", health)]),
            host="127.0.0.1",
            port=port,
            ssl_certfile=str(files.certificate),
            ssl_keyfile=str(files.key),
            log_config=None,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        yield port, files.certificate
        server.should_exit = True
        thread.join(timeout=10)


def test_server_answers_https_health_with_the_generated_certificate(
    https_server: tuple[int, Path],
) -> None:
    port, certificate = https_server
    context = ssl.create_default_context(cafile=str(certificate))
    connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=5)

    connection.request("GET", "/health")
    response = connection.getresponse()

    assert response.status == 200
    assert b"ok" in response.read()
    connection.close()
