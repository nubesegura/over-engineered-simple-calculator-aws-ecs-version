# Architecture

> Status: implemented and deployed in `dev` (2026-10-06); `prod` not deployed yet. The inventory was checked against `modules/` and `environments/` at closing.
> Diagram: [architecture.drawio](architecture.drawio) (export `architecture.png` from it: File > Export as > PNG, 2x).
> Related: [functional.md](functional.md), [technical.md](technical.md), decisions in [adr/](adr/README.md).

## Overview

```
Browser --> Route 53 (weights, owned by webpage) --> ALB (443, JWT rule) --> ECS Fargate services (HTTPS, self-signed)
                                                          |                        |--> RDS PostgreSQL (private, TLS)
                                                          +--> Cognito JWKS (443)  +--> NAT (08:00-22:00) --> ECR, Secrets Manager, SNS, Logs
S3 incoming/*.csv --> EventBridge --> one-off Fargate task (ingest) --> RDS
```

## Resource inventory

Names follow `<acronym>-useast2-oecalc-<descriptor>-<env>` (`<env>` is `dev` or `prod`; `<acct>` is the account ID, built from a variable, never written in files). Context `oecalc` and the five mandatory tags come from `environments/root.hcl`. Region `us-east-2`. Unit = Terragrunt unit that owns the resource. **Both** = `dev` and `prod`; **prod** = only there.

| # | Resource | IaC type | Planned name | Purpose | Env | Unit |
|---|---|---|---|---|---|---|
| 1 | VPC | `aws_vpc` | `vpc-useast2-oecalc-<env>` (Name tag) | Private network, DNS support and hostnames on | Both | network |
| 2 | Public subnets (2 AZ) | `aws_subnet` | `snet-useast2-oecalc-public-a-<env>`, `-b-` | Load balancer nodes and NAT gateway | Both | network |
| 3 | Private subnets (2 AZ) | `aws_subnet` | `snet-useast2-oecalc-private-a-<env>`, `-b-` | Tasks and database | Both | network |
| 4 | Internet gateway | `aws_internet_gateway` | `igw-useast2-oecalc-<env>` | Public subnets to the internet | Both | network |
| 5 | NAT gateway (one, AZ a), **created and deleted daily by the scheduler Lambda, not by Terraform** | created by the function (EC2 API) | `nat-useast2-oecalc-<env>` (Name tag) | Egress of private tasks (ECR, logs, secrets, SNS); absent from 22:00 to 08:00 | Both | scheduler |
| 6 | Elastic IP for NAT | `aws_eip` | `eip-useast2-oecalc-nat-<env>` | Fixed address of the NAT | Both | network |
| 7 | Route tables (public, private) | `aws_route_table` | `rtb-useast2-oecalc-public-<env>`, `-private-` | Routing; the private default route to the NAT is created and deleted by the scheduler Lambda | Both | network |
| 8 | S3 gateway endpoint | `aws_vpc_endpoint` | `vpce-useast2-oecalc-s3-<env>` | Free path to S3 (image layers, ingestion) without NAT traffic | Both | network |
| 9 | Default security group, locked | `aws_default_security_group` | (default, no rules) | VPC-02 | Both | network |
| 10 | Security group, load balancer (prefix `sgp`: AWS forbids names that start with `sg-`) | `aws_security_group` | `sgp-useast2-oecalc-alb-<env>` | 443 and 80 from the internet; egress to tasks (task port) and 443 for the JWKS download of the JWT rule | Both | network |
| 11 | SG services | `aws_security_group` | `sgp-useast2-oecalc-svc-<env>` | Task port only from the ALB group; egress 443 and database port | Both | network |
| 12 | SG jobs (ingest, migrate) | `aws_security_group` | `sgp-useast2-oecalc-job-<env>` | No ingress; egress 443 and database port | Both | network |
| 13 | SG database | `aws_security_group` | `sgp-useast2-oecalc-db-<env>` | 5432 only from services and jobs groups | Both | network |
| 14 | VPC flow log + role + log group | `aws_flow_log`, `aws_iam_role`, `aws_cloudwatch_log_group` | `flog-useast2-oecalc-<env>`, `iamr-useast2-oecalc-flowlog-<env>`, `/oecalc/<env>/vpc-flow` | VPC-01 | prod | network |
| 15 | KMS key + alias | `aws_kms_key`, `aws_kms_alias` | `kms-useast2-oecalc-data-<env>`, `alias/kms-useast2-oecalc-data-<env>` | Encrypts RDS, its secret, S3 ingestion, logs, SNS, ECR | Both | security |
| 16 | SNS topic + email subscription | `aws_sns_topic`, `aws_sns_topic_subscription` | `sns-useast2-oecalc-alerts-<env>` | Errors and alarms to `SUPPORT_EMAIL` | Both | security |
| 17 | ECR repositories (6) + lifecycle | `aws_ecr_repository`, `aws_ecr_lifecycle_policy` | `ecr-useast2-oecalc-<svc>-<env>` for `add`, `sub`, `mul`, `div`, `history`, `ingest` (the migration image is the `history` one with another command) | Images, scan on push, immutable tags, keep 5 | Both | registry |
| 18 | DB subnet group | `aws_db_subnet_group` | `dbsg-useast2-oecalc-<env>` | Private subnets of both AZs | Both | database |
| 19 | DB parameter group | `aws_db_parameter_group` | `dbpg-useast2-oecalc-<env>` | `rds.force_ssl=1` | Both | database |
| 20 | RDS PostgreSQL instance | `aws_db_instance` | `rds-useast2-oecalc-<env>` | db.t4g.micro, Single-AZ, gp3 20 GB, backups 1 day dev and 15 days prod, CMK, managed master password | Both | database |
| 21 | Master secret (created by RDS) + rotation schedule | managed by `aws_db_instance`; schedule by `aws_secretsmanager_secret_rotation` (confirmed) | `rds!db-<id>` (name set by AWS) | Master credential for the administrator, the migration job and the rotation function only; rotates every 3 days | Both | database |
| 21a | Application secret + rotation (alternating users) | `aws_secretsmanager_secret`, `aws_secretsmanager_secret_rotation`, `aws_lambda_permission` | `sm-useast2-oecalc-app-<env>` | Credential of the limited application user used by services and the ingest job; rotates every 3 days | Both | rotation |
| 21b | Rotation function + role + log group | `aws_lambda_function`, `aws_iam_role`, `aws_cloudwatch_log_group` | `fnc-useast2-oecalc-db-rotation-<env>`, `iamr-useast2-oecalc-db-rotation-<env>`, `/oecalc/<env>/lambda/db-rotation` | Python function (`src/rotation/`) for PostgreSQL alternating users, rotation window 09:00-17:00 owner time; runs in the private subnets with the job security group | Both | rotation |
| 22 | Ingestion bucket | `aws_s3_bucket` + config | `bckt-useast2-oecalc-ecs-ingest-<acct>-<env>` | CSV drop (`incoming/`, `processed/`, `rejected/`, `reports/`), EventBridge notifications on, CMK, versioning, Block Public Access, TLS-only policy, lifecycle per prefix (30 days dev; 90, 90 and 180 days prod) | Both | storage |
| 23 | Load balancer log bucket | `aws_s3_bucket` + policy | `bckt-useast2-oecalc-ecs-alblogs-<acct>-<env>` | ALB access logs (30 days dev, 90 prod), SSE-S3, TLS-only policy | Both | storage |
| 24 | ECS cluster | `aws_ecs_cluster` | `ecsc-useast2-oecalc-<env>` | Fargate; Container Insights in prod | Both | cluster |
| 25 | Task definitions (7) | `aws_ecs_task_definition` | `ecst-useast2-oecalc-<svc>-<env>` for `add`, `sub`, `mul`, `div`, `history`, `ingest`, `migrate` | ARM64, 0.25 vCPU, 0.5 GB, read-only root, awslogs | Both | cluster (migrate), services (5), ingestion (ingest) |
| 26 | ECS services (5) | `aws_ecs_service` | `ecss-useast2-oecalc-<svc>-<env>` | One task each, no public IP | Both | services |
| 27 | Task and execution roles (14) | `aws_iam_role` + inline `aws_iam_role_policy` | `iamr-useast2-oecalc-<svc>-task-<env>`, `iamr-useast2-oecalc-<svc>-exec-<env>` | Least privilege per service | Both | cluster, services, ingestion |
| 28 | Log groups (7) | `aws_cloudwatch_log_group` | `/oecalc/<env>/ecs/<svc>` | JSON logs, CMK, 14 days dev, 90 prod | Both | cluster, services, ingestion |
| 29 | Application load balancer | `aws_lb` | `alb-useast2-oecalc-api-<env>` | Internet-facing, deletion protection in prod, drops invalid headers, access logs | Both | edge |
| 30 | Listeners (443, 80) | `aws_lb_listener` | (no name) | 443: certificate of `webpage`, policy `ELBSecurityPolicy-TLS13-1-2-2021-06`, default 404; 80: redirect 301 | Both | edge |
| 31 | Target groups (5) | `aws_lb_target_group` | `tg-useast2-oecalc-<svc>-<env>` | HTTPS to tasks (self-signed certificate, not validated), health route `/health` without token | Both | edge |
| 32 | Listener rules (10) | `aws_lb_listener_rule` | (no name) | Per service: preflight rule (method OPTIONS, no token) and token rule (JWT verification then forward), paths `/api/v1/<op>` and `/api/ecs/v1/<op>` | Both | edge |
| 33 | WAF association | `aws_wafv2_web_acl_association` | (shared ACL by ARN) | Shared regional ACL; created only when `enable_waf` and `WAF_ACL_ARN` are set | prod | edge |
| 34 | SSM parameters published (2) | `aws_ssm_parameter` | `/oecalc/<env>/api-backends/ecs/dns-name`, `/oecalc/<env>/api-backends/ecs/hosted-zone-id` | Target read by `webpage` for the weighted record | Both | edge |
| 35 | EventBridge rule: new CSV | `aws_cloudwatch_event_rule` + target | `evr-useast2-oecalc-ingest-<env>` | Object Created in `incoming/*.csv` runs the ingest task | Both | ingestion |
| 36 | IAM role for the rule | `aws_iam_role` | `iamr-useast2-oecalc-ingest-events-<env>` | `ecs:RunTask` on the ingest task definition, `iam:PassRole` on its two roles | Both | ingestion |
| 37 | EventBridge rule: task failed | `aws_cloudwatch_event_rule` + target | `evr-useast2-oecalc-task-failed-<env>` | Stopped task with non-zero exit to SNS | Both | ingestion |
| 38 | Scheduler Lambda function + log group | `aws_lambda_function`, `aws_cloudwatch_log_group` | `fnc-useast2-oecalc-nat-scheduler-<env>`, `/oecalc/<env>/lambda/nat-scheduler` | Python, arm64, outside the VPC; creates and deletes the NAT gateway and the private default route; idempotent | Both | scheduler |
| 39 | Scheduler IAM roles | `aws_iam_role` + inline `aws_iam_role_policy` | `iamr-useast2-oecalc-nat-fnc-<env>` (function), `iamr-useast2-oecalc-nat-scheduler-<env>` (invoke the function) | Least privilege: NAT gateway and route actions limited by tag, logs; invoke only that function | Both | scheduler |
| 40 | EventBridge Scheduler schedules (2) + invocation of the function at apply and destroy | `aws_scheduler_schedule`, `aws_lambda_invocation` | `evs-useast2-oecalc-nat-off-<env>`, `evs-useast2-oecalc-nat-on-<env>` | 22:00 off and 08:00 on in the owner's time zone (`env.hcl`); apply creates the NAT, destroy removes it | Both | scheduler |
| 41 | CloudWatch alarms (16) | `aws_cloudwatch_metric_alarm` | `alrm-useast2-oecalc-<svc>-cpu-<env>` (5), `alrm-useast2-oecalc-<svc>-healthy-hosts-<env>` (5), `alrm-useast2-oecalc-alb-5xx-<env>`, `alrm-useast2-oecalc-target-5xx-<env>`, `alrm-useast2-oecalc-db-free-storage-<env>`, `alrm-useast2-oecalc-db-connections-<env>`, `alrm-useast2-oecalc-db-cpu-<env>`, `alrm-useast2-oecalc-ingest-failed-invocations-<env>` | Service CPU, healthy hosts per target group, ALB 5XX (sum >= 5 in 5 minutes) and target 5XX (> 5 in 5 minutes), database storage, connections and CPU, EventBridge failed invocations of the ingest rule; all notify the SNS topic | Both | alarms |
| 42 | CloudWatch alarms owned by their modules (2) | `aws_cloudwatch_metric_alarm` | `alrm-useast2-oecalc-nat-scheduler-errors-<env>`, `alrm-useast2-oecalc-db-rotation-errors-<env>` | Errors of the scheduler and rotation functions | Both | scheduler, rotation |
| 43 | EventBridge rule: rotation failed | `aws_cloudwatch_event_rule` + target | `evr-useast2-oecalc-db-rotation-failed-<env>` | Failed rotation of the application or master secret of this environment to SNS | Both | rotation |
| 44 | RDS export log groups (2) | `aws_cloudwatch_log_group` | `/aws/rds/instance/rds-useast2-oecalc-prod/postgresql`, `/aws/rds/instance/rds-useast2-oecalc-prod/upgrade` | PostgreSQL and upgrade logs exported by RDS, CMK, 90 days; none in `dev` (`db_log_exports = []`) | prod | database |
| 45 | Resource policies | `aws_sns_topic_policy`, `aws_secretsmanager_secret_policy` (2), `aws_s3_bucket_policy` (2) | (attached to rows 16, 21, 21a, 22, 23) | SNS publish only from CloudWatch alarms and EventBridge rules of the account; the master secret policy denies reads except to the migrate task, the rotation function and account administrators, the application secret policy denies non-TLS; buckets deny non-TLS | Both | security, cluster, rotation, storage |
| 46 | IAM inline policies | `aws_iam_role_policy` | `iamp-useast2-oecalc-<name>-<env>` for each role of rows 14, 21b, 27, 36, 39 and the migrate roles | Least-privilege policy of each role | Both | various |
| 47 | Routes, associations and security group rules | `aws_route`, `aws_route_table_association`, `aws_vpc_security_group_ingress_rule` / `egress_rule` | (no name) | Public default route to the IGW; subnet associations; one rule resource per entry of rows 10 to 13, including `alb_jwks` (TCP 443 egress of the load balancer group) | Both | network |
| 48 | Migration task definition, roles and log group | in rows 25, 27, 28 | `ecst-useast2-oecalc-migrate-<env>`, `iamr-useast2-oecalc-migrate-{exec,task}-<env>`, `/oecalc/<env>/ecs/migrate` | Run by the pipeline before services are applied | Both | cluster |

Read, not created here: the API certificate (`/oecalc/<env>/api-certificate-arn`, owned by `webpage`), the Cognito pool and app client (variables), the shared WAF ACL (variable, prod), the hosted zone and the weighted records (`webpage`), the Terraform state bucket (created by Terragrunt on first use as in `sls`), the account CloudTrail.

Differences between environments are only in `environments/<env>/env.hcl`:

| Setting | dev | prod |
|---|---|---|
| VPC CIDR | 10.20.0.0/16 | 10.21.0.0/16 |
| Deletion protection (database, ALB) | off | on |
| Database final snapshot, backups | none, 1 day | final snapshot, 15 days |
| RDS log exports | none | `postgresql`, `upgrade` |
| WAF association, VPC flow logs, Container Insights, X-Ray on the functions | off | on |
| Log retention (services, functions) | 14 days | 90 days |
| ALB log retention | 30 days | 90 days |
| Ingestion bucket retention (processed, rejected, reports) | 30, 30, 30 days | 90, 90, 180 days |
| `force_destroy` of buckets and ECR repositories | true | false |
| KMS deletion window | 7 days | 30 days |
| CORS origin | `https://over-engineered-simple-calculator.dev.nube-segura.com` | `https://over-engineered-simple-calculator.nube-segura.com` |

The NAT schedule (22:00 off, 08:00 on, `America/Panama`), the alarm thresholds, task size (0.25 vCPU, 0.5 GB) and the instance class are the same in both.

Note on the load balancer security group: its Terraform `description` still says that it allows egress to the tasks only, although it also has the `alb_jwks` rule (TCP 443). The text is kept on purpose: changing the description of a security group forces its replacement.
