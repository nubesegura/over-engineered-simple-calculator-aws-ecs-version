# One image definition for every container service of the calculator.
# Build context: src/backend.   docker build --build-arg SERVICE=calc-add -t <repo>:<tag> src/backend
# SERVICE is one of: calc-add, calc-sub, calc-mul, calc-div, history, ingest, migrate.
# Base image: python:3.14-slim-trixie, multi-arch index pinned by digest (resolved 2026-10-05),
# pulled for linux/arm64 (the tasks run on Graviton). Refresh tag and digest together.
ARG PYTHON_IMAGE=python:3.14-slim-trixie@sha256:c3e521df8b2b498a7a682e7e18676771cb80c6b75b8699af886b2d554ce40151

# ---- build stage: dependencies and the calculator_core package, no secrets, no build tools kept ----
# hadolint ignore=DL3029
FROM --platform=linux/arm64 ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /build
# Runtime pins only (requirements.txt); the dev tools are never installed.
COPY calculator_core/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt
COPY calculator_core/pyproject.toml ./calculator_core/pyproject.toml
COPY calculator_core/src ./calculator_core/src
RUN pip install --no-deps ./calculator_core

# ---- runtime stage ----
# hadolint ignore=DL3029
FROM --platform=linux/arm64 ${PYTHON_IMAGE} AS runtime
ARG SERVICE
RUN test -n "${SERVICE}" || (echo "build argument SERVICE is required" >&2; exit 1)

# Non-root user without a login shell or home directory.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root migrations /app/migrations
COPY --chown=root:root services/${SERVICE}/main.py /app/main.py

# The code and the migration files are read-only for the process. The only writable path is /tmp,
# a volume mounted by the task definition (self-signed certificate and key are written there).
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MIGRATIONS_DIRECTORY=/app/migrations \
    TLS_DIRECTORY=/tmp

USER 10001:10001
EXPOSE 8443
# No HEALTHCHECK: the load balancer target group checks /health over HTTPS.
ENTRYPOINT ["python", "/app/main.py"]
