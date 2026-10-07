# Architecture

> Status: design stage (2026-10-05). This file holds only the **resource inventory** approved with the design. The full architecture document is written once, at closing.
> Diagram: [architecture.drawio](architecture.drawio) (export `architecture.png` from it: File > Export as > PNG, 2x).
> Decisions, accepted exceptions and the security design are recorded in the ADRs and the technical documentation, written at closing.

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
| 21 | Master secret (created by RDS) + rotation schedule | managed by `aws_db_instance`; schedule by `aws_secretsmanager_secret_rotation` (to be confirmed at implementation) | `rds!db-<id>` (name set by AWS) | Master credential for the administrator, the migration job and the rotation function only; rotates every 3 days | Both | database |
| 21a | Application secret + rotation (alternating users) | `aws_secretsmanager_secret`, `aws_secretsmanager_secret_rotation`, `aws_lambda_permission` | `sm-useast2-oecalc-app-<env>` | Credential of the limited application user used by services and the ingest job; rotates every 3 days | Both | rotation |
| 21b | Rotation function + role + log group | `aws_lambda_function`, `aws_iam_role`, `aws_cloudwatch_log_group` | `fnc-useast2-oecalc-db-rotation-<env>`, `iamr-useast2-oecalc-db-rotation-<env>`, `/oecalc/<env>/lambda/db-rotation` | Python or the AWS rotation template for PostgreSQL alternating users; runs in the private subnets with the job security group | Both | rotation |
| 22 | Ingestion bucket | `aws_s3_bucket` + config | `bckt-useast2-oecalc-ecs-ingest-<acct>-<env>` | CSV drop (`incoming/`, `processed/`, `rejected/`, `reports/`), EventBridge notifications on | Both | storage |
| 23 | Load balancer log bucket | `aws_s3_bucket` + policy | `bckt-useast2-oecalc-ecs-alblogs-<acct>-<env>` | ALB access logs (30 days dev, 90 prod) | Both | storage |
| 24 | ECS cluster | `aws_ecs_cluster` | `ecsc-useast2-oecalc-<env>` | Fargate; Container Insights in prod | Both | cluster |
| 25 | Task definitions (7) | `aws_ecs_task_definition` | `ecst-useast2-oecalc-<svc>-<env>` for `add`, `sub`, `mul`, `div`, `history`, `ingest`, `migrate` | ARM64, 0.25 vCPU, 0.5 GB, read-only root, awslogs | Both | cluster (migrate), services (5), ingestion (ingest) |
| 26 | ECS services (5) | `aws_ecs_service` | `ecss-useast2-oecalc-<svc>-<env>` | One task each, no public IP | Both | services |
| 27 | Task and execution roles (14) | `aws_iam_role` + inline `aws_iam_role_policy` | `iamr-useast2-oecalc-<svc>-task-<env>`, `iamr-useast2-oecalc-<svc>-exec-<env>` | Least privilege per service | Both | cluster, services, ingestion |
| 28 | Log groups (7) | `aws_cloudwatch_log_group` | `/oecalc/<env>/ecs/<svc>` | JSON logs, CMK, 14 days dev, 90 prod | Both | cluster, services, ingestion |
| 29 | Application load balancer | `aws_lb` | `alb-useast2-oecalc-api-<env>` | Internet-facing, deletion protection in prod, drops invalid headers, access logs | Both | edge |
| 30 | Listeners (443, 80) | `aws_lb_listener` | (no name) | 443: certificate of `webpage`, TLS 1.3 policy, default 404; 80: redirect 301 | Both | edge |
| 31 | Target groups (5) | `aws_lb_target_group` | `tg-useast2-oecalc-<svc>-<env>` | HTTPS to tasks, health route without token | Both | edge |
| 32 | Listener rules (10) | `aws_lb_listener_rule` | (no name) | Per service: preflight rule (method OPTIONS, no token) and token rule (JWT verification then forward), paths `/api/v1/<op>` and `/api/ecs/v1/<op>` | Both | edge |
| 33 | WAF association | `aws_wafv2_web_acl_association` | (shared ACL by ARN) | Shared regional ACL | prod | edge |
| 34 | SSM parameters published (2) | `aws_ssm_parameter` | `/oecalc/<env>/api-backends/ecs/dns-name`, `/oecalc/<env>/api-backends/ecs/hosted-zone-id` | Target read by `webpage` for the weighted record | Both | edge |
| 35 | EventBridge rule: new CSV | `aws_cloudwatch_event_rule` + target | `evr-useast2-oecalc-ingest-<env>` | Object Created in `incoming/*.csv` runs the ingest task | Both | ingestion |
| 36 | IAM role for the rule | `aws_iam_role` | `iamr-useast2-oecalc-ingest-events-<env>` | `ecs:RunTask` on the ingest task definition, `iam:PassRole` on its two roles | Both | ingestion |
| 37 | EventBridge rule: task failed | `aws_cloudwatch_event_rule` + target | `evr-useast2-oecalc-task-failed-<env>` | Stopped task with non-zero exit to SNS | Both | ingestion |
| 38 | Scheduler Lambda function + log group | `aws_lambda_function`, `aws_cloudwatch_log_group` | `fnc-useast2-oecalc-nat-scheduler-<env>`, `/oecalc/<env>/lambda/nat-scheduler` | Python, arm64, outside the VPC; creates and deletes the NAT gateway and the private default route; idempotent | Both | scheduler |
| 39 | Scheduler IAM roles | `aws_iam_role` + inline `aws_iam_role_policy` | `iamr-useast2-oecalc-nat-fnc-<env>` (function), `iamr-useast2-oecalc-nat-scheduler-<env>` (invoke the function) | Least privilege: NAT gateway and route actions limited by tag, logs; invoke only that function | Both | scheduler |
| 40 | EventBridge Scheduler schedules (2) + invocation of the function at apply and destroy | `aws_scheduler_schedule`, `aws_lambda_invocation` | `evs-useast2-oecalc-nat-off-<env>`, `evs-useast2-oecalc-nat-on-<env>` | 22:00 off and 08:00 on in the owner's time zone (`env.hcl`); apply creates the NAT, destroy removes it | Both | scheduler |
| 41 | CloudWatch alarms (16) | `aws_cloudwatch_metric_alarm` | `alrm-useast2-oecalc-<svc>-cpu-<env>` (5), `alrm-useast2-oecalc-<svc>-healthy-hosts-<env>` (5), `alrm-useast2-oecalc-alb-5xx-<env>`, `alrm-useast2-oecalc-target-5xx-<env>`, `alrm-useast2-oecalc-db-free-storage-<env>`, `alrm-useast2-oecalc-db-connections-<env>`, `alrm-useast2-oecalc-db-cpu-<env>`, `alrm-useast2-oecalc-ingest-failed-invocations-<env>` | Service CPU, healthy hosts per target group, ALB and target 5XX, database storage, connections and CPU, EventBridge failed invocations of the ingest rule; all notify the SNS topic | Both | alarms |
| 42 | CloudWatch alarms owned by their modules (2) | `aws_cloudwatch_metric_alarm` | `alrm-useast2-oecalc-nat-scheduler-errors-<env>`, `alrm-useast2-oecalc-db-rotation-errors-<env>` | Errors of the scheduler and rotation functions | Both | scheduler, rotation |
| 43 | EventBridge rule: rotation failed | `aws_cloudwatch_event_rule` + target | `evr-useast2-oecalc-db-rotation-failed-<env>` | Failed rotation of the application or master secret of this environment to SNS | Both | rotation |

Read, not created here: the API certificate (`/oecalc/<env>/api-certificate-arn`, owned by `webpage`), the Cognito pool and app client (variables), the shared WAF ACL (variable, prod), the hosted zone and the weighted records (`webpage`), the Terraform state bucket (created by Terragrunt on first use as in `sls`), the account CloudTrail.

Differences between environments are only in `environments/<env>/env.hcl`: deletion protection, final snapshot, backup retention (1 day dev, 15 days prod), WAF association, flow logs, Container Insights, log retention, RDS log exports, and the time zone of the NAT schedule.
