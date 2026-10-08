"""Wires and starts one HTTP service: container, ASGI app and uvicorn with self-signed TLS."""

import os
from pathlib import Path

import uvicorn
from starlette.types import ASGIApp

from calculator_core.adapters.inbound.http_app import (
    CalculationRoute,
    HttpAppConfig,
    create_app,
)
from calculator_core.adapters.inbound.self_signed_tls import self_signed_files
from calculator_core.config.container import Container, build_container
from calculator_core.config.observability import configure_logging, set_request_context
from calculator_core.config.settings import BACKEND_NAME, load_settings
from calculator_core.domain.operation import Operation

PORT = 8443
# Longer than the load balancer idle timeout (60 s by default), or it would reuse closed sockets.
KEEP_ALIVE_SECONDS = 65
DEFAULT_TLS_DIRECTORY = "/tmp"  # noqa: S108 - the writable volume the tasks mount at /tmp
_CALCULATION_SERVICES = {
    "calc-add": Operation.ADD,
    "calc-sub": Operation.SUB,
    "calc-mul": Operation.MUL,
    "calc-div": Operation.DIV,
}
SERVICES = (*_CALCULATION_SERVICES, "history")


def build_service_app(service: str, container: Container) -> ASGIApp:
    """Mount only the routes of `service`."""
    if service not in SERVICES:
        raise ValueError(f"Unknown service: {service}.")
    settings = container.settings
    config = HttpAppConfig(
        service_name=settings.service_name,
        environment=settings.environment,
        cors_allowed_origin=settings.cors_allowed_origin,
        backend_name=BACKEND_NAME,
    )
    operation = _CALCULATION_SERVICES.get(service)
    return create_app(
        config,
        container.notifier,
        set_request_context,
        calculation=CalculationRoute(operation, container.calculate) if operation else None,
        history=container.read_history if service == "history" else None,
    )


def run_http_service(service: str) -> None:
    settings = load_settings(os.environ)
    configure_logging(settings.service_name, settings.environment)
    app = build_service_app(service, build_container(settings))
    tls_parent = Path(os.environ.get("TLS_DIRECTORY", DEFAULT_TLS_DIRECTORY))
    with self_signed_files(settings.service_name, tls_parent) as files:
        uvicorn.run(
            app,
            host="0.0.0.0",  # noqa: S104 - task interface; the security group limits access
            port=PORT,
            ssl_certfile=str(files.certificate),
            ssl_keyfile=str(files.key),
            timeout_keep_alive=KEEP_ALIVE_SECONDS,
            log_config=None,
            access_log=False,
        )
