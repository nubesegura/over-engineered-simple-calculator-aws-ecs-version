# Technical documentation

## Stack

- Python 3.14, Starlette 1.7 and Uvicorn, psycopg 3 with `psycopg-pool`, boto3, `cryptography`. Versions are pinned in `src/backend/calculator_core/requirements*.txt`.
- Hexagonal package `src/backend/calculator_core/` (`domain`, `application` use cases and ports, `adapters` inbound and outbound, `config`). The import contracts (`.importlinter`) keep the layers apart.
- One image definition, `src/backend/Dockerfile`, with the build argument `SERVICE` (`calc-add`, `calc-sub`, `calc-mul`, `calc-div`, `history`, `ingest`, `migrate`); base `python:3.14-slim-trixie` pinned by digest, arm64, non-root user (10001), code read-only with `/tmp` as the only writable volume.
- Infrastructure: Terraform modules in `modules/`, Terragrunt units in `environments/` (`common/` shared definitions, `dev/` and `prod/` with the same units; every difference is in `env.hcl`). Region `us-east-2`, one AWS account per environment. The resource inventory is in [architecture.md](architecture.md).

Decisions: [adr/](adr/README.md).

## Services

Each HTTP service mounts only its routes under `/api/v1` and `/api/ecs/v1`, plus `/health`, and listens on TCP 8443 over HTTPS with a throwaway self-signed certificate generated at start (`adapters/inbound/self_signed_tls.py`, written under `TLS_DIRECTORY`; [ADR 0005](adr/0005-self-signed-target-certificates.md)). Cross-cutting behavior (correlation id, CORS headers on every response, error shape) is one ASGI middleware. A service fails fast when a required setting is missing, except with `ENVIRONMENT=local`.

Environment variables of the HTTP services: `ENVIRONMENT`, `SERVICE_NAME`, `APP_SECRET_ARN`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `ALERT_TOPIC_ARN`, `CORS_ALLOWED_ORIGIN`, `TLS_DIRECTORY`. The ingest job needs `INGEST_BUCKET` and `INGEST_KEY` (set by the EventBridge target). The migrate task uses `MASTER_SECRET_ARN`, `APP_SECRET_ARN`, `DB_HOST`, `DB_PORT`, `DB_NAME` and `MIGRATIONS_DIRECTORY`.

## Database

PostgreSQL 17 on RDS (`db.t4g.micro`, gp3 20 GB, Single-AZ, CMK, `rds.force_ssl=1`, private subnets). Schema through versioned SQL in `src/backend/migrations/` (`0001_create_calculations.sql`, `0002_application_role.sql`), applied by the `migrate` task from the pipeline; services never change the schema.

Identities: the master user (`calcadmin`, secret managed by RDS, rotated every 3 days) is read only by the migrate task, the rotation function and account administrators (secret policy). Services and the ingest job use `calc_app`, a login user that belongs to the group role `calc_app_rw` (select and insert on `calculations`). Its secret `sm-useast2-oecalc-app-<env>` rotates every 3 days with the alternating-user strategy through `src/rotation/` (a Lambda in the private subnets). Clients keep a pool and reconnect with the current secret version when a new connection is rejected. See [ADR 0004](adr/0004-alternating-user-rotation.md).

## Edge

The ALB listens on 443 (policy `ELBSecurityPolicy-TLS13-1-2-2021-06`, certificate read from SSM `/oecalc/<env>/api-certificate-arn`, published by the webpage repository; the edge unit refuses to plan when it is empty). The default action is a JSON 404. Per service there are two listener rules: a preflight rule (method `OPTIONS`, no token; priorities 10 to 50) and a token rule (priorities 110 to 150) whose first action is `jwt-validation` (issuer, JWKS of the pool, `aud`, `token_use`) and second a forward. HTTP 80 redirects to HTTPS. Target groups use HTTPS to the task port with a `/health` check.

The load balancer security group needs egress TCP 443 (rule `alb_jwks`) to download the JWKS; without it the rule returns a 500 produced by the load balancer (`JWKSRequestTimeout`). Its Terraform description remains "egress to the tasks only" because editing it forces replacement of the group. See [ADR 0002](adr/0002-token-validation-at-the-load-balancer.md).

In `prod` the shared regional WAF ACL (`WAF_ACL_ARN`) is associated. The edge unit publishes `/oecalc/<env>/api-backends/ecs/dns-name` and `/oecalc/<env>/api-backends/ecs/hosted-zone-id` for the webpage repository.

## Network and the NAT schedule

Two public and two private subnets, an S3 gateway endpoint and one NAT gateway in AZ a. The `scheduler` unit holds an EventBridge Scheduler pair (`cron(0 22 * * ? *)` off, `cron(0 8 * * ? *)` on, `America/Panama`) that invokes a Lambda (`src/scheduler/`) which creates or deletes the NAT gateway and the private default route, idempotently; the Elastic IP stays. Terraform invokes the function at apply (NAT on) and at destroy (NAT off), so the state never owns the NAT. The deployment workflow also invokes it with `{"action":"on"}` before building and pushing images. Errors raise an alarm. See [ADR 0003](adr/0003-scheduled-nat-gateway.md).

## Delivery

`.github/workflows/`: `ci.yml` (lint, format, types, import contracts, tests with coverage of at least 85 percent, Terraform and Terragrunt checks; Checkov is disabled for now), `deploy-dev.yml` (branch `develop`) and `deploy-prod.yml` (branch `main`, approval on the GitHub `prod` environment), both calling `deploy.yml`.

Steps of `deploy.yml`: validate variables and branch, verify the account, phase 1 apply (`security`, `network`, `scheduler`, `registry`, `storage`, `database`, `rotation`, `cluster`), NAT on, build and push the images tagged with the commit, run the migrate task, phase 2 apply (`edge`, `services`, `ingestion`, `alarms`), and a smoke test through the load balancer (no token returns 401, preflight returns the allowed origin). The smoke test does not use a valid token, so it cannot detect the JWKS egress problem; the 5XX alarm of the load balancer covers it.

GitHub environment secrets `ROLE_ARN`, `AWS_ACCOUNT_ID`, `SUPPORT_EMAIL`; variables `AWS_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `WAF_ACL_ARN` (`prod`). AWS access is by OIDC with the role `github-actions-terraform-<env>-role`.

## Local checks

From `src/backend/calculator_core`: `ruff check . && ruff format --check .`, `mypy`, `PYTHONPATH=src lint-imports`, `pytest` (PostgreSQL tests run when `TEST_DATABASE_URL` is set). Infrastructure: `terraform fmt -check -recursive`, `terragrunt hcl fmt --check`, `terraform validate` per module.

## Known exceptions (owner-accepted for this project)

Single-AZ database, no AWS Backup plan, no RDS Proxy, tokens verified only at the load balancer, no administrator access path to the database, no Secrets Manager endpoint (a night rotation may leave new connections failing until the NAT returns), S3 server access logging and Macie not enabled, Checkov disabled.

## Operational lessons

- A change of a force-new attribute (for example a security group `description`) on a resource in use makes Terraform delete its rules and fail with `DependencyViolation`. Scan plans for `forces replacement` before pushing.
- Security group names cannot start with `sg-`: the acronym is `sgp`.
- Terragrunt copies only the module folder, so modules receive source paths as inputs built from `get_repo_root()`.
