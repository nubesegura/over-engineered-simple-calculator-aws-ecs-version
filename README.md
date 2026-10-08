# Over-engineered simple calculator - ECS version

The container-based backend of the calculator: the same business rules and API contract as the serverless sibling
(`over-engineered-simple-calculator-aws-sls-version`), built on Amazon ECS Fargate and Amazon RDS for PostgreSQL to try a
technology other than serverless. The web page lives in `over-engineered-simple-calculator-webpage` and does not change when
traffic moves between backends: Route 53 weights, owned by the webpage repository, decide which backend answers.

## What is here

- Five HTTP services (`calc-add`, `calc-sub`, `calc-mul`, `calc-div`, `history`) behind one internet-facing Application Load
  Balancer. The load balancer verifies the Cognito ID token before a request reaches a service.
- A CSV ingestion job: a file dropped in `incoming/` of the ingestion bucket starts one Fargate task through EventBridge.
- A PostgreSQL database (Single-AZ, private). Services use a limited application user whose credential rotates every 3 days;
  the master user is only for administration, migrations and the rotation function.
- A NAT gateway that a scheduler removes from 22:00 to 08:00 (Panama time) to save cost.

The API answers on `/api/v1` (shared by every backend) and `/api/ecs/v1` (its own prefix): `POST /<add|sub|mul|div>` and
`GET /history`. Every method except the CORS preflight needs `Authorization: Bearer <Cognito ID token>`.

## Layout

| Path | Content |
|---|---|
| `src/backend/calculator_core/` | Python package (hexagonal: domain, application, adapters, config), tests and import contracts |
| `src/backend/services/<service>/` | Thin entrypoints of the containers; one `src/backend/Dockerfile` with the build argument `SERVICE` |
| `src/backend/migrations/` | Versioned SQL migrations |
| `src/scheduler/`, `src/rotation/` | Lambda functions: NAT scheduler and database credential rotation |
| `modules/` | Terraform modules |
| `environments/` | Terragrunt root, common units and `dev` / `prod` settings (`env.hcl`) |
| `.github/workflows/` | `ci.yml` and the deployment workflows (`develop` deploys `dev`, `main` deploys `prod`) |
| `docs/` | [Functional](docs/functional.md), [technical](docs/technical.md), [architecture](docs/architecture.md) (resource inventory and diagram) and [decisions](docs/adr/README.md) |

## Checks

From `src/backend/calculator_core` (Python 3.14, dependencies pinned in `requirements*.txt`):

```
ruff check . && ruff format --check .
mypy
PYTHONPATH=src lint-imports
pytest            # PostgreSQL tests run when TEST_DATABASE_URL is set
```

Infrastructure: `terraform fmt -check -recursive`, `terragrunt hcl fmt --check` (from `environments/`), `terraform validate`
per module, `tflint` and `checkov`; the CI runs them on every push.

## Deployment

GitHub environment `dev` or `prod` needs the secrets `ROLE_ARN`, `AWS_ACCOUNT_ID` and `SUPPORT_EMAIL`, and the variables
`AWS_REGION`, `COGNITO_USER_POOL_ID` and `COGNITO_APP_CLIENT_ID` (`WAF_ACL_ARN` in `prod`). The webpage repository must have
published the API certificate (`/oecalc/<env>/api-certificate-arn`) first. The workflow applies the foundation units, builds and
pushes the images, runs the migration, applies the load balancer, services, ingestion and alarms, and ends with a smoke test.

Test project: raise it, review it and destroy it. Retirement order (weights back to `sls` first, deletion protection lifted in `prod`) is in [docs/functional.md](docs/functional.md#retirement-of-the-test-deployment).

## Destroying the dev environment

The manual workflow **Destroy DEV** (`.github/workflows/destroy-dev.yml`) tears down this repository's `dev` environment. There are no approvers (single-user organization), so the safety is inside the workflow:

1. Run it from the `develop` branch (Actions, Destroy DEV, Run workflow). The default mode is **plan**: it lists what would be destroyed and touches nothing.
2. Read the summary of the run. To destroy, run it again with mode **destroy** and type the name of this repository in `confirm`.
3. It uses the `dev` GitHub environment and the dev AWS account only; the account is verified before anything runs. There is no destroy workflow for `prod`.

Teardown order across the repositories: the backends (`ecs`, `sls`) first, then the web page (`webpage`), then `shared-resources` (certificate and DNS of the API). The Terraform state bucket and the GitHub variables and secrets are not removed.

Notes: the database and the buckets of dev are deleted with their data (dev has no deletion protection and no final snapshot). If a security group fails to delete with `DependencyViolation` because of the network interfaces of the rotation function, wait some minutes and run the workflow again.
