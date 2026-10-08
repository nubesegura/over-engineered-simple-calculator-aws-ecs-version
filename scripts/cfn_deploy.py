"""CloudFormation (SAM) deployment driver of the IAC_TOOL=cloudformation path.

It deploys the stacks of templates/stacks.json in order, one Terragrunt unit per stack, with a change set
guard equal to the Terraform delete guard: any Remove action, or any replacement (Replacement True or
Conditional) of a resource type that is not in REPLACEMENT_ALLOWED_TYPES, stops the deployment before the
change set is executed.

Commands:

    deploy   --env dev --phase 1      build and package SAM stacks, create, check and execute change sets
    output   --env dev --unit cluster --key ClusterName    print one stack output
    destroy  --env dev --mode plan|destroy                 list or delete the stacks in reverse order
    params   --env dev --unit edge                         print the parameters a stack would receive

Parameter values come from parameters/<env>.json (the only place with environment values) and, for the
values that live in GitHub, from these environment variables: IMAGE_TAG (ImageTag), SUPPORT_EMAIL
(AlertEmail), COGNITO_USER_POOL_ID, COGNITO_APP_CLIENT_ID and WAF_ACL_ARN. The five mandatory tags of
parameters/<env>.json are applied as stack tags (CloudFormation propagates them to the resources) and
also fill the Tag* parameters of the scheduler stack.

It writes to AWS only when run by the pipeline (deploy and destroy). Standard library plus the AWS and
SAM CLIs through subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
STACKS_FILE = REPO_ROOT / "templates" / "stacks.json"
BUILD_DIR = REPO_ROOT / ".aws-sam"

ENV_OVERRIDES = {
    "ImageTag": "IMAGE_TAG",
    "AlertEmail": "SUPPORT_EMAIL",
    "CognitoUserPoolId": "COGNITO_USER_POOL_ID",
    "CognitoAppClientId": "COGNITO_APP_CLIENT_ID",
    "WafAclArn": "WAF_ACL_ARN",
}
TAG_PARAMETERS = {
    "TagTeamOwner": "team-owner",
    "TagProjectName": "project-name",
    "TagAppName": "app-name",
    "TagRepoName": "repo-name",
}
NO_CHANGES = ("didn't contain changes", "No updates are to be performed", "submitted information didn't contain")
# Resource types whose replacement (True or Conditional) the change set guard lets through. Every entry needs
# a reason here:
#   AWS::ECS::TaskDefinition: task definitions are immutable; every new image tag registers a new revision,
#   exactly what Terraform does (aws_ecs_task_definition "must be replaced"). Nothing stateful is lost.
REPLACEMENT_ALLOWED_TYPES = frozenset({"AWS::ECS::TaskDefinition"})
POLL_SECONDS = 15
STACK_TIMEOUT_SECONDS = 90 * 60


class DeployError(Exception):
    """A stack could not be deployed or checked safely."""


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------
def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        raise DeployError(f"{' '.join(command[:3])} failed: {completed.stderr.strip()[:2000]}")
    return completed


def aws_json(args: list[str], *, check: bool = True) -> Any:
    completed = run(["aws", *args, "--output", "json"], check=check)
    if completed.returncode != 0:
        return None
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def load_stacks(env: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(STACKS_FILE.read_text(encoding="utf-8"))
    return data, data["environments"][env]


def load_parameters(env: str) -> dict[str, Any]:
    """parameters/<env>.json, refused when its EnvType is another environment (prod values never reach dev)."""
    config: dict[str, Any] = json.loads((REPO_ROOT / "parameters" / f"{env}.json").read_text(encoding="utf-8"))
    env_type = config.get("Parameters", {}).get("EnvType")
    if env_type != env:
        raise DeployError(f"parameters/{env}.json has EnvType '{env_type}': it must be '{env}'")
    return config


def template_parameters(template: Path) -> dict[str, str | None]:
    """Parameter names of a template and their Default (None when there is none).

    The templates of this repository keep a fixed layout (two-space indentation), so the top-level
    Parameters block is read without a YAML library.
    """
    names: dict[str, str | None] = {}
    inside = False
    current: str | None = None
    for line in template.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\S", line):
            inside = line.startswith("Parameters:")
            current = None
            continue
        if not inside:
            continue
        key = re.match(r"^  ([A-Za-z0-9]+):\s*$", line)
        if key:
            current = key.group(1)
            names[current] = None
            continue
        default = re.match(r'^    Default:\s*"?(.*?)"?\s*$', line)
        if default and current:
            names[current] = default.group(1)
    return names


def stack_parameters(env: str, template: Path) -> list[dict[str, str]]:
    config = load_parameters(env)
    values: dict[str, str] = {k: str(v) for k, v in config["Parameters"].items()}
    for parameter, tag in TAG_PARAMETERS.items():
        values[parameter] = config["Tags"][tag]
    for parameter, variable in ENV_OVERRIDES.items():
        if os.environ.get(variable) is not None:
            values[parameter] = os.environ[variable]
    result: list[dict[str, str]] = []
    missing: list[str] = []
    for name, default in template_parameters(template).items():
        if name in values:
            result.append({"ParameterKey": name, "ParameterValue": values[name]})
        elif default is None:
            missing.append(name)
    if missing:
        raise DeployError(f"{template.name}: no value for parameters {missing}")
    return result


def stack_status(stack: str) -> str | None:
    completed = run(
        ["aws", "cloudformation", "describe-stacks", "--stack-name", stack, "--output", "json"], check=False
    )
    if completed.returncode != 0:
        if "does not exist" in completed.stderr:
            return None
        raise DeployError(f"cannot describe stack {stack}: {completed.stderr.strip()}")
    return str(json.loads(completed.stdout)["Stacks"][0]["StackStatus"])


def stack_outputs(stack: str) -> dict[str, str]:
    data = aws_json(["cloudformation", "describe-stacks", "--stack-name", stack])
    return {o["OutputKey"]: o["OutputValue"] for o in data["Stacks"][0].get("Outputs", [])}


def print_failed_events(stack: str) -> None:
    data = aws_json(
        ["cloudformation", "describe-stack-events", "--stack-name", stack, "--max-items", "40"], check=False
    )
    for event in (data or {}).get("StackEvents", []):
        if event.get("ResourceStatus", "").endswith("_FAILED"):
            print(f"  {event['LogicalResourceId']} {event['ResourceStatus']}: {event.get('ResourceStatusReason', '')}")


def wait_stack(stack: str, expected: tuple[str, ...]) -> None:
    deadline = time.monotonic() + STACK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = stack_status(stack)
        if status is None and "DELETE_COMPLETE" in expected:
            return
        if status and not status.endswith("_IN_PROGRESS"):
            if status in expected:
                return
            print_failed_events(stack)
            raise DeployError(f"stack {stack} ended in {status}")
        time.sleep(POLL_SECONDS)
    raise DeployError(f"timed out waiting for stack {stack}")


# ---------------------------------------------------------------------------
# Change set guard
# ---------------------------------------------------------------------------
def guard_changes(
    changes: list[dict[str, Any]], allowed_replacements: frozenset[str] = REPLACEMENT_ALLOWED_TYPES
) -> list[str]:
    """Return the violations of the change set guard (empty means safe to execute)."""
    violations: list[str] = []
    for change in changes:
        detail = change.get("ResourceChange", {})
        action = detail.get("Action")
        logical = detail.get("LogicalResourceId")
        resource_type = detail.get("ResourceType")
        replacement = detail.get("Replacement")
        if action == "Remove":
            violations.append(f"Remove {resource_type} {logical}")
        elif replacement in ("True", "Conditional") and resource_type not in allowed_replacements:
            violations.append(f"Replace ({replacement}) {resource_type} {logical}")
    return violations


def describe_change_set(stack: str, name: str) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        args = ["cloudformation", "describe-change-set", "--stack-name", stack, "--change-set-name", name]
        if token:
            args += ["--next-token", token]
        page = aws_json(args)
        changes.extend(page.get("Changes", []))
        token = page.get("NextToken")
        if not token:
            page["Changes"] = changes
            return page


def wait_change_set(stack: str, name: str) -> dict[str, Any] | None:
    """Wait for the change set; None means it holds no changes."""
    while True:
        page = describe_change_set(stack, name)
        status = page.get("Status")
        if status == "CREATE_COMPLETE":
            return page
        if status == "FAILED":
            reason = page.get("StatusReason", "")
            if any(marker in reason for marker in NO_CHANGES):
                run(["aws", "cloudformation", "delete-change-set", "--stack-name", stack, "--change-set-name", name])
                return None
            raise DeployError(f"change set of {stack} failed: {reason}")
        time.sleep(5)


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------
def prepare_template(env: str, entry: dict[str, Any]) -> Path:
    """Template file to deploy: SAM templates are built and packaged (code to the SAM bucket)."""
    template = REPO_ROOT / entry["template"]
    if not entry.get("sam"):
        return template
    unit = entry["unit"]
    build_dir = BUILD_DIR / "build" / unit
    packaged = BUILD_DIR / "packaged" / f"{unit}.yaml"
    packaged.parent.mkdir(parents=True, exist_ok=True)
    common = ["--config-env", env, "--config-file", str(REPO_ROOT / "samconfig.toml")]
    run(["sam", "build", "--template-file", str(template), "--build-dir", str(build_dir), *common])
    run(
        [
            "sam",
            "package",
            "--template-file",
            str(build_dir / "template.yaml"),
            "--output-template-file",
            str(packaged),
            *common,
        ]
    )
    return packaged


def change_set_name(run_id: str, attempt: str, unit: str) -> str:
    """Unique per run and attempt, so a re-run of the same run id never collides with a kept change set."""
    safe = re.sub(r"[^A-Za-z0-9-]", "-", f"iac-{run_id}-{attempt}-{unit}")
    return safe[:128]


def deploy_stack(env: str, entry: dict[str, Any], run_id: str, attempt: str) -> None:
    stack = entry["stack"]
    print(f"::group::{stack}")
    status = stack_status(stack)
    if status in ("ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "DELETE_FAILED") or (status or "").endswith("_IN_PROGRESS"):
        raise DeployError(f"stack {stack} is in {status}: investigate it (read-only) before deploying again")
    change_set_type = "CREATE" if status in (None, "REVIEW_IN_PROGRESS") else "UPDATE"
    template = prepare_template(env, entry)
    parameters = stack_parameters(env, REPO_ROOT / entry["template"])
    tags = [{"Key": k, "Value": v} for k, v in load_parameters(env)["Tags"].items()]
    name = change_set_name(run_id, attempt, entry["unit"])
    with tempfile.TemporaryDirectory() as scratch:
        params_file = Path(scratch) / "parameters.json"
        tags_file = Path(scratch) / "tags.json"
        params_file.write_text(json.dumps(parameters), encoding="utf-8")
        tags_file.write_text(json.dumps(tags), encoding="utf-8")
        run(
            [
                "aws",
                "cloudformation",
                "create-change-set",
                "--stack-name",
                stack,
                "--change-set-name",
                name,
                "--change-set-type",
                change_set_type,
                "--template-body",
                f"file://{template}",
                "--parameters",
                f"file://{params_file}",
                "--tags",
                f"file://{tags_file}",
                "--capabilities",
                "CAPABILITY_IAM",
                "CAPABILITY_NAMED_IAM",
                "CAPABILITY_AUTO_EXPAND",
            ]
        )
    page = wait_change_set(stack, name)
    if page is None:
        print(f"{stack}: no changes")
    else:
        for change in page["Changes"]:
            d = change.get("ResourceChange", {})
            print(
                f"  {d.get('Action'):<7} {d.get('ResourceType'):<45} {d.get('LogicalResourceId')}"
                f" replacement={d.get('Replacement', '-')}"
            )
        violations = guard_changes(page["Changes"])
        if violations:
            for violation in violations:
                print(f"::error::Change set guard: {stack}: {violation}")
            raise DeployError(f"change set guard refused {stack}; the change set {name} was left for review")
        run(["aws", "cloudformation", "execute-change-set", "--stack-name", stack, "--change-set-name", name])
        wait_stack(stack, ("CREATE_COMPLETE", "UPDATE_COMPLETE"))
        print(f"{stack}: {change_set_type.lower()} complete")
    if env == "prod":
        run(
            [
                "aws",
                "cloudformation",
                "update-termination-protection",
                "--enable-termination-protection",
                "--stack-name",
                stack,
            ]
        )
    if entry["unit"] == "network":
        lock_default_security_group(env, stack)
    print("::endgroup::")


def lock_default_security_group(env: str, stack: str) -> None:
    """Remove every rule of the VPC default security group and tag it, like aws_default_security_group.

    CloudFormation cannot manage the default group of a VPC (VPC-02), so the pipeline does it after the
    network stack. Idempotent.
    """
    group_id = stack_outputs(stack)["DefaultSecurityGroupId"]
    rules = aws_json(["ec2", "describe-security-group-rules", "--filters", f"Name=group-id,Values={group_id}"])
    ingress = [r["SecurityGroupRuleId"] for r in rules["SecurityGroupRules"] if not r["IsEgress"]]
    egress = [r["SecurityGroupRuleId"] for r in rules["SecurityGroupRules"] if r["IsEgress"]]
    if ingress:
        run(
            [
                "aws",
                "ec2",
                "revoke-security-group-ingress",
                "--group-id",
                group_id,
                "--security-group-rule-ids",
                *ingress,
            ]
        )
    if egress:
        run(
            ["aws", "ec2", "revoke-security-group-egress", "--group-id", group_id, "--security-group-rule-ids", *egress]
        )
    config = load_parameters(env)
    name = f"sgp-{config['Parameters']['RegionCode']}-{config['Parameters']['Context']}-default-{env}"
    tags = [f"Key=Name,Value={name}"] + [f"Key={k},Value={v}" for k, v in config["Tags"].items()]
    run(["aws", "ec2", "create-tags", "--resources", group_id, "--tags", *tags])
    print(f"Default security group locked: {len(ingress)} ingress and {len(egress)} egress rules removed")


def command_deploy(args: argparse.Namespace) -> int:
    _, entries = load_stacks(args.env)
    load_parameters(args.env)  # fails before any change set when the file belongs to another environment
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    for entry in entries:
        if entry["phase"] == args.phase:
            deploy_stack(args.env, entry, run_id, attempt)
    return 0


# ---------------------------------------------------------------------------
# Destroy (dev only)
# ---------------------------------------------------------------------------
def empty_bucket(bucket: str) -> int:
    """Delete every object version and delete marker of a versioned bucket (dev teardown only)."""
    removed = 0
    while True:
        page = aws_json(["s3api", "list-object-versions", "--bucket", bucket, "--max-items", "1000"], check=False)
        if page is None:
            return removed
        objects = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for kind in ("Versions", "DeleteMarkers")
            for item in page.get(kind) or []
        ]
        if not objects:
            return removed
        with tempfile.TemporaryDirectory() as scratch:
            batch = Path(scratch) / "delete.json"
            batch.write_text(json.dumps({"Objects": objects, "Quiet": True}), encoding="utf-8")
            result = aws_json(["s3api", "delete-objects", "--bucket", bucket, "--delete", f"file://{batch}"])
        # Quiet mode returns only failures: any entry stops the job instead of looping until the timeout.
        errors = (result or {}).get("Errors") or []
        if errors:
            sample = "; ".join(f"{e.get('Key')}: {e.get('Code')} {e.get('Message', '')}".strip() for e in errors[:5])
            raise DeployError(f"cannot empty bucket {bucket}: {len(errors)} objects not deleted ({sample})")
        removed += len(objects)


def nat_off(stack: str) -> None:
    """Remove the NAT gateway and wait (the Terraform destroy invocation sends tf.action = delete)."""
    function = stack_outputs(stack)["FunctionName"]
    with tempfile.TemporaryDirectory() as scratch:
        response = Path(scratch) / "response.json"
        completed = run(
            [
                "aws",
                "lambda",
                "invoke",
                "--function-name",
                function,
                "--payload",
                '{"action":"off","tf":{"action":"delete"}}',
                "--cli-binary-format",
                "raw-in-base64-out",
                "--cli-read-timeout",
                "900",
                "--query",
                "FunctionError",
                "--output",
                "text",
                str(response),
            ]
        )
    if completed.stdout.strip() != "None":
        raise DeployError(f"the scheduler function failed when removing the NAT gateway ({completed.stdout.strip()})")
    print("NAT gateway removed (scheduler invoked with action off)")


def stack_resources(stack: str) -> list[dict[str, Any]]:
    data = aws_json(["cloudformation", "list-stack-resources", "--stack-name", stack])
    return [r for r in data.get("StackResourceSummaries", []) if r.get("ResourceStatus") != "DELETE_COMPLETE"]


def processed_template(stack: str) -> dict[str, Any]:
    """Deployed template after the transforms (DeletionPolicy and EmptyOnDelete per logical ID); {} if unreadable."""
    data = aws_json(
        ["cloudformation", "get-template", "--stack-name", stack, "--template-stage", "Processed"], check=False
    )
    body = (data or {}).get("TemplateBody")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return {}
    return body if isinstance(body, dict) else {}


def termination_protection(stack: str) -> bool:
    data = aws_json(["cloudformation", "describe-stacks", "--stack-name", stack])
    return bool(data["Stacks"][0].get("EnableTerminationProtection"))


def bucket_has_objects(bucket: str) -> bool:
    page = aws_json(["s3api", "list-object-versions", "--bucket", bucket, "--max-items", "1"], check=False)
    return bool(page and (page.get("Versions") or page.get("DeleteMarkers")))


def repository_has_images(repository: str) -> bool:
    page = aws_json(["ecr", "list-images", "--repository-name", repository, "--max-items", "1"], check=False)
    return bool(page and page.get("imageIds"))


def empty_on_delete(template: dict[str, Any], logical_id: str) -> bool:
    value = template.get("Resources", {}).get(logical_id, {}).get("Properties", {}).get("EmptyOnDelete")
    return value is True or str(value).lower() == "true"


def empty_repository(repository: str) -> int:
    """Delete every image of one repository (only when EmptyOnDelete is not set on it)."""
    removed = 0
    while True:
        page = aws_json(["ecr", "list-images", "--repository-name", repository, "--max-items", "100"], check=False)
        ids = (page or {}).get("imageIds") or []
        if not ids:
            return removed
        with tempfile.TemporaryDirectory() as scratch:
            batch = Path(scratch) / "images.json"
            batch.write_text(json.dumps(ids), encoding="utf-8")
            run(["aws", "ecr", "batch-delete-image", "--repository-name", repository, "--image-ids", f"file://{batch}"])
        removed += len(ids)


def plan_stack(entry: dict[str, Any], status: str) -> list[str]:
    """Markdown lines: status, resources, what would be retained and what blocks a delete."""
    stack = entry["stack"]
    resources = stack_resources(stack)
    template = processed_template(stack)
    definitions = template.get("Resources", {})
    lines = [f"- `{stack}` ({status}): {len(resources)} resources"]
    lines.extend(f"  - {r['ResourceType']} {r['LogicalResourceId']}" for r in resources)
    retained = [
        f"{r['LogicalResourceId']} ({definitions.get(r['LogicalResourceId'], {}).get('DeletionPolicy')})"
        for r in resources
        if definitions.get(r["LogicalResourceId"], {}).get("DeletionPolicy")
        in ("Retain", "RetainExceptOnCreate", "Snapshot")
    ]
    if template:
        lines.append(f"  - Retained on delete: {', '.join(retained) if retained else 'nothing'}")
    else:
        lines.append("  - Retained on delete: unknown (processed template not readable)")
    blockers: list[str] = []
    if termination_protection(stack):
        blockers.append("termination protection (turned off by destroy, dev only)")
    for r in resources:
        physical = r.get("PhysicalResourceId", "")
        if r["ResourceType"] == "AWS::S3::Bucket" and physical and bucket_has_objects(physical):
            blockers.append(f"bucket {physical} is not empty (emptied by destroy, all versions)")
        if r["ResourceType"] == "AWS::ECR::Repository" and physical and repository_has_images(physical):
            how = "EmptyOnDelete" if empty_on_delete(template, r["LogicalResourceId"]) else "emptied by destroy"
            blockers.append(f"repository {physical} holds images ({how})")
    lines.append(f"  - Blocks a delete: {'; '.join(blockers) if blockers else 'nothing'}")
    return lines


def destroy_stack(entry: dict[str, Any]) -> None:
    stack = entry["stack"]
    print(f"::group::Delete {stack}")
    if termination_protection(stack):
        run(
            [
                "aws",
                "cloudformation",
                "update-termination-protection",
                "--no-enable-termination-protection",
                "--stack-name",
                stack,
            ]
        )
        print("Termination protection turned off (dev)")
    if entry["unit"] == "scheduler":
        nat_off(stack)
    resources = stack_resources(stack)
    template = processed_template(stack)
    for r in resources:
        physical = r.get("PhysicalResourceId", "")
        if not physical:
            continue
        # Only the buckets and repositories of THIS stack (list-stack-resources), never by name pattern.
        if r["ResourceType"] == "AWS::S3::Bucket":
            print(f"Emptied {physical}: {empty_bucket(physical)} object versions and delete markers removed")
        if r["ResourceType"] == "AWS::ECR::Repository" and not empty_on_delete(template, r["LogicalResourceId"]):
            print(f"Emptied {physical}: {empty_repository(physical)} images removed")
    run(["aws", "cloudformation", "delete-stack", "--stack-name", stack])
    wait_stack(stack, ("DELETE_COMPLETE",))
    print(f"{stack}: deleted")
    print("::endgroup::")


def command_destroy(args: argparse.Namespace) -> int:
    if args.env != "dev":
        raise DeployError("destroy is only supported for dev")
    load_parameters(args.env)  # EnvType of the parameter file must be the environment being destroyed
    _, entries = load_stacks(args.env)
    present = [(entry, stack_status(entry["stack"])) for entry in reversed(entries)]
    present = [(entry, status) for entry, status in present if status is not None]
    summary = ["### Destroy DEV with CloudFormation", "", f"Stacks present (reverse deploy order): {len(present)}", ""]
    for entry, status in present:
        summary.extend(plan_stack(entry, status or ""))
    print("\n".join(summary))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")
    if args.mode != "destroy":
        print("Nothing was deleted (mode plan).")
        return 0
    for entry, _ in present:
        destroy_stack(entry)
    return 0


# ---------------------------------------------------------------------------
# Read-only helpers
# ---------------------------------------------------------------------------
def find_entry(env: str, unit: str) -> dict[str, Any]:
    _, entries = load_stacks(env)
    for entry in entries:
        if entry["unit"] == unit:
            return entry
    raise DeployError(f"unknown unit {unit}")


def command_output(args: argparse.Namespace) -> int:
    print(stack_outputs(find_entry(args.env, args.unit)["stack"])[args.key])
    return 0


def command_params(args: argparse.Namespace) -> int:
    entry = find_entry(args.env, args.unit)
    for item in stack_parameters(args.env, REPO_ROOT / entry["template"]):
        hidden = item["ParameterKey"] in ("AlertEmail",)
        print(f"{item['ParameterKey']}={'***' if hidden else item['ParameterValue']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--env", required=True, choices=["dev", "prod"])
    deploy.add_argument("--phase", required=True, type=int, choices=[1, 2])
    deploy.set_defaults(handler=command_deploy)
    destroy = sub.add_parser("destroy")
    destroy.add_argument("--env", required=True, choices=["dev"])
    destroy.add_argument("--mode", required=True, choices=["plan", "destroy"])
    destroy.set_defaults(handler=command_destroy)
    output = sub.add_parser("output")
    output.add_argument("--env", required=True, choices=["dev", "prod"])
    output.add_argument("--unit", required=True)
    output.add_argument("--key", required=True)
    output.set_defaults(handler=command_output)
    params = sub.add_parser("params")
    params.add_argument("--env", required=True, choices=["dev", "prod"])
    params.add_argument("--unit", required=True)
    params.set_defaults(handler=command_params)
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except DeployError as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
