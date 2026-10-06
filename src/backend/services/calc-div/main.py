"""Entrypoint of the calc-div service: HTTPS on port 8443 (the logic lives in calculator_core)."""

from calculator_core.config.http_service import run_http_service

if __name__ == "__main__":
    run_http_service("calc-div")
