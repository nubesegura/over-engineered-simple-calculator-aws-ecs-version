# CloudFormation and SAM twin of the infrastructure

The infrastructure of this repository can be deployed with **Terraform** (Terragrunt units under
`environments/`, modules under `modules/`) or with **CloudFormation** (the SAM and CloudFormation
templates in this folder). Both build the same resources with the same names and tags; the Terraform code
is the specification. The decision and its limits are in
[ADR 0007](../docs/adr/0007-dual-iac-terraform-and-cloudformation.md).

Only one tool may own an environment at a time. The pipeline enforces it (owner guard, below).

## The switch: `IAC_TOOL`

| GitHub variable | Values | Default when unset | Where |
|---|---|---|---|
| `IAC_TOOL` | `terraform`, `cloudformation` | `terraform` | GitHub environment `dev` and `prod` (variable, not secret) |

`deploy.yml` (called by `deploy-dev.yml` and `deploy-prod.yml`) reads
`${{ vars.IAC_TOOL || 'terraform' }}` and fails on any other value. `IAC_TOOL` is the intent for the next
deployment: `destroy-dev.yml` does not use it to decide anything (see below). With `terraform` (or the variable unset)
the Terraform steps run exactly as before. With `cloudformation` the SAM/CloudFormation steps run instead,
with the same OIDC role, account check, concurrency group, image build, migration task and smoke test.
`cloudformation` means "SAM or CloudFormation, whichever each template needs": SAM is CloudFormation with a
transform.

The other values the stacks need and that live in GitHub are the same as for Terraform:
`SUPPORT_EMAIL` (secret), `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `WAF_ACL_ARN` (prod) and the image
tag (the commit SHA).

## Layout

```
templates/<unit>.yaml      one stack per Terragrunt unit, environment-agnostic
templates/stacks.json      stack names per environment in deploy order (read by the scripts)
parameters/dev.json        the ONLY place with environment values (Parameters) and the five tags (Tags)
parameters/prod.json       same keys as dev.json
samconfig.toml             SAM build and package settings, one [dev] and one [prod] section
scripts/cfn_deploy.py      deploy (change sets + guard), outputs, destroy (dev)
scripts/iac_owner_guard.py one IaC tool per environment (fail closed)
scripts/iac_inventory.py   normalized inventory of an environment, to diff Terraform against CloudFormation
```

Two templates are SAM (`scheduler.yaml`, `rotation.yaml`): their Lambda code is built by `sam build`
from `src/scheduler/nat_scheduler` and `src/rotation/db_rotation` (pinned dependencies of
`requirements.txt`) and uploaded by `sam package`. The others are plain CloudFormation;
`registry`, `edge`, `services` and `alarms` use the `AWS::LanguageExtensions` transform (`Fn::ForEach`
per service). Container images are not a
reason for SAM: the pipeline builds and pushes them and the stacks receive the tag as the `ImageTag`
parameter.

## Stacks and deploy order

Stack names follow the naming convention (`stk-<region>-<context>-<unit>-<env>`). Data crosses stacks
through `Outputs` with `Export` and `Fn::ImportValue` (export names `<stack>:<Output>`).

| # | Stack (dev) | Template | Phase | Imports from |
|---|---|---|---|---|
| 1 | `stk-useast2-oecalc-security-dev` | `security.yaml` | 1 | - |
| 2 | `stk-useast2-oecalc-network-dev` | `network.yaml` | 1 | security |
| 3 | `stk-useast2-oecalc-scheduler-dev` | `scheduler.yaml` (SAM) | 1 | security, network |
| 4 | `stk-useast2-oecalc-registry-dev` | `registry.yaml` | 1 | security |
| 5 | `stk-useast2-oecalc-storage-dev` | `storage.yaml` | 1 | security |
| 6 | `stk-useast2-oecalc-database-dev` | `database.yaml` | 1 | security, network |
| 7 | `stk-useast2-oecalc-rotation-dev` | `rotation.yaml` (SAM) | 1 | security, network, database |
| 8 | `stk-useast2-oecalc-cluster-dev` | `cluster.yaml` | 1 | security, registry, database, rotation |
| - | NAT on, build and push 7 images, migration task (pipeline steps, not stacks) | | | |
| 9 | `stk-useast2-oecalc-edge-dev` | `edge.yaml` | 2 | network, storage |
| 10 | `stk-useast2-oecalc-services-dev` | `services.yaml` | 2 | security, network, registry, database, rotation, cluster, edge |
| 11 | `stk-useast2-oecalc-ingestion-dev` | `ingestion.yaml` | 2 | security, network, registry, storage, database, rotation, cluster |
| 12 | `stk-useast2-oecalc-alarms-dev` | `alarms.yaml` | 2 | security, database, cluster, edge, services, ingestion |

Prod uses the same list with `-prod`. Destroy runs the list in reverse order.

## What the pipeline does on the CloudFormation path

1. Validates `IAC_TOOL`, the branch, the environment configuration, assumes the OIDC role and checks the account.
2. **Owner guard** (`scripts/iac_owner_guard.py check --tool "$IAC_TOOL" --env <env>`), before anything else.
   `parameters/<env>.json` must carry `EnvType` = `<env>` (checked before any change set or delete).
3. Phase 1: for each stack, `sam build` and `sam package` (SAM templates only), then a change set
   (`create-change-set`, name `iac-<run id>-<run attempt>-<unit>`), the **change set guard** (any `Remove`, or
   any `Replacement` `True` or `Conditional` of a type outside `REPLACEMENT_ALLOWED_TYPES` in
   `scripts/cfn_deploy.py`, today only `AWS::ECS::TaskDefinition`, stops the job and leaves the change set for
   review), `execute-change-set`
   and a wait. Prod stacks get termination protection. After the network stack, the VPC default security
   group is emptied of rules and tagged (what `aws_default_security_group` does in Terraform).
4. NAT on (invokes the scheduler function with `{"action":"on"}`; it also replaces the apply-time
   `aws_lambda_invocation` of Terraform), image build and push, migration task.
5. Phase 2 with the same change set flow, then the smoke test through the load balancer.

Destroy DEV (`destroy-dev.yml`) follows what is deployed, not `IAC_TOOL`. After the account check it runs
`scripts/iac_owner_guard.py detect --env dev` and branches on the owner: `terraform` runs the Terragrunt plan
and destroy unchanged; `cloudformation` runs `scripts/cfn_deploy.py destroy`; `none` ends green ("nothing is
deployed"); `both` or an unreadable owner fails. It prints the configured `IAC_TOOL` next to the detected
owner and, when they differ, a notice that the destroy removes the owner's deployment and that a deploy with
the variable's tool works once dev is empty. An invalid or unset `IAC_TOOL` never blocks a destroy.

CloudFormation path: mode `plan` lists every stack in reverse deploy order with its status, its resources,
what it would retain (`DeletionPolicy` Retain or Snapshot) and what blocks a delete (non-empty buckets, ECR
repositories with images, termination protection). Mode `destroy` (typed confirmation), per stack in reverse
order: termination protection off (dev only), NAT gateway off through the scheduler function before its
stack (as the Terraform destroy invocation), empty the S3 buckets of that stack only (from
`list-stack-resources`, all versions and delete markers), ECR repositories empty themselves
(`EmptyOnDelete: true` in dev; a repository without it is emptied the same scoped way), then `delete-stack`
and wait for `stack-delete-complete`. On `DELETE_FAILED` only the `*_FAILED` events are printed and the job
fails. There is no destroy workflow for prod.

## Owner guard (one tool per environment)

`check --tool "$IAC_TOOL" --env <env>` runs first in `deploy.yml` (deploys and plans) and fails closed:

| Detected owner | Requested tool | Exit | Result |
|---|---|---|---|
| none | any | 0 | allowed |
| same as requested | | 0 | allowed |
| the other tool | | 2 | stops: destroy with the owner first |
| both | | 3 | stops: inconsistent, fix by hand |
| cannot read stacks or state | | 4 | stops (fail closed) |

CloudFormation owns the environment when any stack of `stacks.json` exists in any status except
`DELETE_COMPLETE`. Terraform owns it when any unit state object
(`<repo>/<env>/<unit>/terraform.tfstate` in `bckt-<region code>-tf-state-<env>-<account>`) holds managed
resources. Only counts and stack names are printed. Because the default of the variable is `terraform`, an
environment running on CloudFormation is protected even when the variable was never set.

`detect --env <env>` prints one line `owner=terraform|cloudformation|none|both` (exit 0, 0, 0, 3; exit 4 and
no line when unreadable) and is what `destroy-dev.yml` branches on.

## Switch procedure (dev)

1. Change the `IAC_TOOL` variable of the `dev` GitHub environment (a deploy now stops with exit 2 until dev is empty).
2. Run **Destroy DEV** in mode `plan`, then `destroy`: it detects the owner and destroys with that tool.
3. Run **Deploy DEV** (push to `develop` or manual dispatch).
4. Optional proof of parity: run `python scripts/iac_inventory.py --env dev --output <file>` after a deploy
   with each tool and diff the two files (keep them outside the repository).

**Prod switching is not supported** without a data migration plan (database contents, ingestion bucket,
secrets and their rotation state, ALB DNS name published to the webpage repository). Prod stays on the tool
it was first deployed with.

## Known differences with Terraform

Listed with their options in the parity report that the owner keeps locally. In short:
the VPC default security group is locked by a pipeline step (CloudFormation cannot manage it); the
database security group carries one loopback egress rule (127.0.0.1/32) so that EC2 does not add its
allow-all default; the load balancer log bucket policy uses the log delivery service principal unless
`ElbLogDeliveryAccountId` is set; the application secret is deleted without a recovery window in prod
too; S3 and ECR teardown in dev is done by the pipeline and by `EmptyOnDelete`; the database instance is
declared twice with opposite `EnvType` conditions (prod `Snapshot`, dev `Delete`); SAM uses its own managed
artifact bucket (`resolve_s3`), outside the naming convention.

To verify at the first dev deployment: update the stack twice and confirm the application secret value is
unchanged (`GenerateSecretString` must not rewrite a password that the rotation function already changed).

## Local checks

```
cfn-lint --regions us-east-2 -- templates/*.yaml
sam validate --lint --region us-east-2 --template-file templates/scheduler.yaml
sam validate --lint --region us-east-2 --template-file templates/rotation.yaml
python -m unittest discover -s scripts/tests
```

## ALB access log bucket (parity with Terraform)

`ElbLogDeliveryAccountId` in `parameters/<env>.json` is the AWS-owned regional account of Elastic Load Balancing for us-east-2 (public, documented by AWS; it is not an account of this project). Terraform resolves the same value with `aws_elb_service_account`. With the value set, the bucket policy is identical to the Terraform one; empty it to use the log delivery service principal instead.
