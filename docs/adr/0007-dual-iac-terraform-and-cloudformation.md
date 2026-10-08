# ADR 0007: Dual IaC, Terraform and CloudFormation

Status: Proposed (2026-10-07)

## Context

The team standard is one repository, one IaC tool. This is a practice repository, and the owner wants to
deploy the same infrastructure with Terraform (Terragrunt, the original tool) or with CloudFormation (SAM,
because two Lambda functions are packaged from the repository) to compare both tools on a real workload.

## Decision

- Exception to "one repository, one tool" for this repository only, by owner decision. The Terraform code
  under `modules/` and `environments/` stays the specification; the templates under `templates/` replicate
  it resource by resource, with the same names and tags, and the differences are listed in the parity report.
- A GitHub variable `IAC_TOOL` per environment selects the tool: `terraform` (default when unset) or
  `cloudformation` (SAM or plain CloudFormation, whichever each template needs). Any other value fails the
  workflow. With `terraform` the pipeline behaves as before.
- One tool per environment at a time. Both tools create the same names, so they can never coexist in an
  environment. An owner guard (`scripts/iac_owner_guard.py`) runs first in the deploy and destroy workflows,
  detects the owner from what exists (CloudFormation stacks, non-empty Terraform state objects) without
  marker tags, and stops the job when the environment belongs to the other tool, when both own resources,
  or when ownership cannot be read (fail closed).
- Switching an environment means destroying it with the owner tool, changing `IAC_TOOL` and deploying with
  the other. Only `dev` can be switched; switching `prod` is not supported without a data migration plan.
- The CloudFormation path keeps the deployment order and the non-IaC steps (NAT on, image build and push,
  migration task, smoke test) and guards every change set against deletions and replacements, like the
  Terraform path.

## Consequences

Two definitions of the same infrastructure must be kept in step: every infrastructure change is made in
Terraform first and replicated in the templates, and CI lints both. Some Terraform behaviors have no
CloudFormation equivalent (the VPC default security group, the bucket `force_destroy`, the secret recovery
window, the apply and destroy invocations of the NAT function); they are covered by pipeline steps or
accepted as documented differences. The CloudFormation deployment role needs CloudFormation and SAM
artifact permissions that the Terraform role does not.
