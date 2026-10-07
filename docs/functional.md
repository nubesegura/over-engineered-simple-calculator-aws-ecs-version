# Functional documentation

The ECS version of the calculator is a backend that implements the same API contract as the serverless sibling (`over-engineered-simple-calculator-aws-sls-version`). The web page (`over-engineered-simple-calculator-webpage`) calls the API host name of its environment with the Cognito ID token; which backend answers is decided only by the Route 53 weights owned by the webpage repository. The history of this backend starts empty (no data is migrated).

## API

Base paths: `/api/v1` (neutral, shared by every backend) and `/api/ecs/v1` (own prefix). Both behave identically. All requests and responses are JSON.

| Method and path | Purpose |
|---|---|
| `POST /{add, sub, mul, div}` | Body `{"a": <number>, "b": <number>}`. Returns `200` with `calculation_id`, `operation`, `a`, `b`, `result` (strings) |
| `GET /history?limit=<n>&cursor=<opaque>` | Latest calculations first: `items` (`calculation_id`, `operation`, `a`, `b`, `result`, `occurred_at`) and `next_cursor` (null at the end). `limit` defaults to 20, capped at 100 |
| `GET /health` | Used by the load balancer only; needs no token |
| `OPTIONS <any API path>` | CORS preflight; needs no token; answers with the allowed origin of the environment |

Rules: operands within +-1e15 and at most 15 decimal places, computed with `Decimal`. A calculation is saved before the `200` is returned; saving the same `calculation_id` again keeps one row. A saved row is visible to the next history read.

### Errors

Shape: `{"error": {"code", "message", "request_id"}}`.

| Status | When | Notifies the owner |
|---|---|---|
| 400 | Malformed body or missing operand (`VALIDATION_ERROR`), invalid operand, division by zero (`DIVISION_BY_ZERO`), invalid `limit` or `cursor` | No |
| 401 | No valid Cognito ID token. Produced by the load balancer, so its body is not the JSON shape above | No |
| 404, 405 | Unknown route or method | No |
| 500 | Unexpected or persistence error, generic message | Yes (SNS email) |

## Access

Every method except the preflight and `/health` needs `Authorization: Bearer <Cognito ID token>`. The load balancer verifies the signature against the pool JWKS, the issuer, the expiry, `aud` equal to the app client and `token_use` equal to `id`; rejected requests never reach a service. The pool and the app client belong to the webpage repository.

## CSV ingestion

The owner drops a `.csv` file in `incoming/` of the ingestion bucket. EventBridge starts one Fargate task for that object, with no queue and no permanent task.

- Header: `operation,operand_a,operand_b,occurred_at` (ISO-8601 UTC). Maximum 5 MB.
- Each valid row is saved with a deterministic id from its content, so ingesting a file twice creates no duplicates.
- Invalid rows are skipped and listed (line and reason) in a report under `reports/`.
- When the run ends the file moves to `processed/`. A file that is not valid CSV, has a wrong header or is too large moves to `rejected/` with a report and an email.
- An infrastructure failure (database, S3) fails the task, leaves the file in `incoming/` and sends an email.

## Moving traffic between backends

Route 53 weights decide the backend (for example `sls` 100 and `ecs` 0, or the reverse). They are variables of the webpage repository; a change needs a deployment of that repository and is reversible by changing the weights again. To test `ecs` before the switch, resolve the API host name to the load balancer on the tester's machine. See [ADR 0006](adr/0006-route-53-weights-owned-by-webpage.md).

## Operations

- Alerts: an SNS email on infrastructure errors, failed ingestion tasks, failed secret rotation and the alarms listed in [architecture.md](architecture.md) (unhealthy service, CPU, 5XX of the load balancer or the targets, database storage, connections and CPU).
- Night window: the NAT gateway is removed from 22:00 to 08:00 (Panama time). While it is down, tasks cannot reach ECR, Secrets Manager, SNS or CloudWatch Logs: running services keep answering, but a new task, a new database connection after a credential rotation, an email alert or an ingestion run can fail or wait until 08:00.
- Credentials: the services use a limited database user whose secret rotates every 3 days between 09:00 and 17:00 (Panama time).
- No administrator access path to the database exists at present (owner decision); the options are to be discussed when needed.

## Retirement of the test deployment

Do the steps in this order; each is reversible until the database is destroyed.

1. In the webpage repository set the weights back to `sls` (100) and `ecs` (0) and deploy it; confirm the web page works against `sls`. Never destroy a backend that still receives traffic.
2. Optional: take a manual database snapshot if the data matters (`prod` takes a final snapshot by itself; `dev` does not).
3. `prod` only: lift the deletion protection. In `environments/prod/env.hcl` set `db_deletion_protection = false` and `alb_deletion_protection = false` and apply the `database` and `edge` units. Buckets and ECR repositories are not force-destroyed in `prod` (`force_destroy_data = false`): empty the ingestion and ALB log buckets and the ECR repositories by hand, or set it to `true` and apply `storage` and `registry` first.
4. Destroy the units in reverse order of the deployment: `alarms`, `ingestion`, `services`, `edge`, `cluster`, `rotation`, `database`, `storage`, `registry`, `scheduler`, `network`, `security` (for example `terragrunt run --all destroy` from `environments/<env>`). The destroy of `scheduler` removes the NAT gateway through its function, since the Terraform state never owns it.
5. The webpage repository then skips the backend because its SSM parameters no longer exist; remove the entry from its list of registered backends.
6. The KMS key stays pending deletion for 7 days (`dev`) or 30 days (`prod`); the RDS final snapshot of `prod` stays until the owner deletes it.

The rehearsal of the rollback to `sls` and the `prod` deployment are pending at the time of writing; this procedure has not been executed yet.
