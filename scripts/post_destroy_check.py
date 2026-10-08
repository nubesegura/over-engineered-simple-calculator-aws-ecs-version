#!/usr/bin/env python3
"""Read-only check that nothing of a repository is left in an environment after a destroy.

1. Resource Groups Tagging API: every resource tagged repo-name=<repository> (and env-type=<env>).
2. Kinds the tagging API does not return or that carry no tags: EventBridge Scheduler schedules, active ECS task
   definition families, IAM roles by naming convention, secrets (also those scheduled for deletion), KMS keys
   (also pending deletion) and log groups by naming convention.
3. Known and expected residues are labelled, not hidden: KMS keys pending deletion, secrets scheduled for deletion.

Usage: python post_destroy_check.py --repo-name <repository> --env dev [--profile P] [--region us-east-2] [--context oecalc]
Exit code 0 when nothing unexpected is left, 1 otherwise, 2 when a read fails. Only list and describe calls are made.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any


def aws(args: list[str], profile: str | None, region: str) -> dict[str, Any]:
    cmd = ["aws", *args, "--region", region, "--output", "json"] + (
        ["--profile", profile] if profile else []
    )
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"aws {' '.join(args[:2])} failed: {re.sub(r'[0-9]{12}', '************', result.stderr.strip())[:200]}"
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


_CACHE: dict[str, set[str]] = {}


def is_stale(arn: str, profile: str | None, region: str) -> bool:
    """True when the tagging index still lists a resource that no longer exists or is only a tombstone.

    The tagging API lags behind deletes; ECS keeps INACTIVE clusters, services and task definitions and
    STOPPED tasks visible for a while; NAT gateways stay 'deleted' for about an hour. Unknown types are never stale.
    """
    svc, rest = arn.split(":")[2], arn.split(":", 5)[5]
    try:
        if svc == "ecs":
            kind, _, name = rest.partition("/")
            if kind == "task-definition":
                if "active_td" not in _CACHE:
                    _CACHE["active_td"] = set(
                        aws(
                            ["ecs", "list-task-definitions", "--status", "ACTIVE"],
                            profile,
                            region,
                        ).get("taskDefinitionArns", [])
                    )
                return arn not in _CACHE["active_td"]
            cluster = name.split("/")[0]
            if kind == "cluster":
                found = aws(
                    ["ecs", "describe-clusters", "--clusters", arn], profile, region
                ).get("clusters", [])
                return not found or found[0]["status"] == "INACTIVE"
            if kind == "service":
                found = aws(
                    [
                        "ecs",
                        "describe-services",
                        "--cluster",
                        cluster,
                        "--services",
                        arn,
                    ],
                    profile,
                    region,
                ).get("services", [])
                return not found or found[0]["status"] == "INACTIVE"
            if kind == "task":
                found = aws(
                    ["ecs", "describe-tasks", "--cluster", cluster, "--tasks", arn],
                    profile,
                    region,
                ).get("tasks", [])
                return not found or found[0]["lastStatus"] == "STOPPED"
        if svc == "ec2":
            kind, _, rid = rest.partition("/")
            if kind == "natgateway":
                found = aws(
                    ["ec2", "describe-nat-gateways", "--nat-gateway-ids", rid],
                    profile,
                    region,
                ).get("NatGateways", [])
                return not found or found[0]["State"] == "deleted"
            if kind == "vpc-endpoint":
                found = aws(
                    ["ec2", "describe-vpc-endpoints", "--vpc-endpoint-ids", rid],
                    profile,
                    region,
                ).get("VpcEndpoints", [])
                return not found or found[0]["State"] in ("deleted", "deleting")
            by_id = {
                "security-group": (
                    "describe-security-groups",
                    "--group-ids",
                    "SecurityGroups",
                ),
                "subnet": ("describe-subnets", "--subnet-ids", "Subnets"),
                "vpc": ("describe-vpcs", "--vpc-ids", "Vpcs"),
                "route-table": (
                    "describe-route-tables",
                    "--route-table-ids",
                    "RouteTables",
                ),
                "internet-gateway": (
                    "describe-internet-gateways",
                    "--internet-gateway-ids",
                    "InternetGateways",
                ),
                "elastic-ip": ("describe-addresses", "--allocation-ids", "Addresses"),
            }
            if kind in by_id:
                cmd, flag, key = by_id[kind]
                return not aws(["ec2", cmd, flag, rid], profile, region).get(key)
            if kind == "security-group-rule":
                return not aws(
                    [
                        "ec2",
                        "describe-security-group-rules",
                        "--security-group-rule-ids",
                        rid,
                    ],
                    profile,
                    region,
                ).get("SecurityGroupRules")
    except RuntimeError as exc:
        return (
            "NotFound" in str(exc)
            or "does not exist" in str(exc)
            or "MISSING" in str(exc)
        )
    return False


def short(arn: str) -> str:
    parts = arn.split(":", 5)
    return f"{parts[2]}:{parts[5]}" if len(parts) > 5 else arn


def collect(
    a: argparse.Namespace,
) -> tuple[
    list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]], list[str]
]:
    suffix = f"-{a.env}"
    left: list[tuple[str, str]] = []  # unexpected
    expected: list[tuple[str, str]] = []  # known residues
    stale: list[
        tuple[str, str]
    ] = []  # listed by the tagging index but already gone or INACTIVE/STOPPED
    skipped: list[str] = []  # checks that could not run (missing permission)

    tagged = aws(
        [
            "resourcegroupstaggingapi",
            "get-resources",
            "--tag-filters",
            f"Key=repo-name,Values={a.repo_name}",
            f"Key=env-type,Values={a.env}",
        ],
        a.profile,
        a.region,
    )
    for item in tagged.get("ResourceTagMappingList", []):
        arn = item["ResourceARN"]
        if ":kms:" in arn and ":key/" in arn:
            state = aws(["kms", "describe-key", "--key-id", arn], a.profile, a.region)[
                "KeyMetadata"
            ]["KeyState"]
            (expected if state == "PendingDeletion" else left).append(
                ("kms key", f"{short(arn)} ({state})")
            )
        elif is_stale(arn, a.profile, a.region):
            stale.append(("stale tag index entry", short(arn)))
        else:
            left.append(("tagged resource", short(arn)))

    def optional(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except RuntimeError as exc:
            if "AccessDenied" in str(exc) or "not authorized" in str(exc):
                skipped.append(name)
            else:
                raise

    def schedules() -> None:
        for s in aws(["scheduler", "list-schedules"], a.profile, a.region).get(
            "Schedules", []
        ):
            if a.context in s["Name"] and s["Name"].endswith(suffix):
                left.append(("scheduler schedule", s["Name"]))

    def task_definitions() -> None:
        fam = aws(
            ["ecs", "list-task-definition-families", "--status", "ACTIVE"],
            a.profile,
            a.region,
        ).get("families", [])
        left.extend(
            ("ecs task definition (active)", f)
            for f in fam
            if a.context in f and f.endswith(suffix)
        )

    def iam_roles() -> None:
        for r in aws(["iam", "list-roles"], a.profile, a.region).get("Roles", []):
            if a.context in r["RoleName"] and r["RoleName"].endswith(suffix):
                tags = {
                    t["Key"]: t["Value"]
                    for t in aws(
                        ["iam", "list-role-tags", "--role-name", r["RoleName"]],
                        a.profile,
                        a.region,
                    ).get("Tags", [])
                }
                if tags.get("repo-name", a.repo_name) == a.repo_name:
                    left.append(("iam role", r["RoleName"]))

    def secrets() -> None:
        for s in aws(
            ["secretsmanager", "list-secrets", "--include-planned-deletion"],
            a.profile,
            a.region,
        ).get("SecretList", []):
            tags = {t["Key"]: t["Value"] for t in s.get("Tags", [])}
            if (
                a.context in s["Name"]
                and s["Name"].endswith(suffix)
                and tags.get("repo-name", a.repo_name) == a.repo_name
            ):
                (expected if s.get("DeletedDate") else left).append(
                    (
                        "secret",
                        s["Name"]
                        + (" (scheduled for deletion)" if s.get("DeletedDate") else ""),
                    )
                )

    def log_groups() -> None:
        for g in aws(
            ["logs", "describe-log-groups", "--log-group-name-pattern", a.context],
            a.profile,
            a.region,
        ).get("logGroups", []):
            name = g["logGroupName"]
            if name.endswith(suffix) or f"{suffix}/" in name:
                tags = aws(
                    [
                        "logs",
                        "list-tags-for-resource",
                        "--resource-arn",
                        g["arn"].rstrip(":*"),
                    ],
                    a.profile,
                    a.region,
                ).get("tags", {})
                if tags.get("repo-name", a.repo_name) == a.repo_name:
                    left.append(("log group", name))

    for name, fn in (
        ("scheduler", schedules),
        ("ecs task definitions", task_definitions),
        ("iam roles", iam_roles),
        ("secrets", secrets),
        ("log groups", log_groups),
    ):
        optional(name, fn)
    return left, expected, stale, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-name", required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--profile")
    ap.add_argument("--region", default="us-east-2")
    ap.add_argument(
        "--context",
        default="oecalc",
        help="project context segment of the naming convention",
    )
    ap.add_argument(
        "--retries",
        type=int,
        default=1,
        help="passes while something is left (deletes can lag)",
    )
    ap.add_argument("--wait", type=int, default=60, help="seconds between passes")
    a = ap.parse_args()
    try:
        for attempt in range(1, max(a.retries, 1) + 1):
            left, expected, stale, skipped = collect(a)
            if not left or attempt == a.retries:
                break
            print(f"pass {attempt}: {len(left)} left, waiting {a.wait}s", flush=True)
            time.sleep(a.wait)
    except (
        RuntimeError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        print(f"cannot complete the check: {exc}")
        return 2

    print(f"Post-destroy check: repo-name={a.repo_name} env={a.env} (read-only)")
    for title, rows in (
        ("LEFT OVER", left),
        ("Expected residues (not an error)", expected),
        (
            "Tombstones already gone (not an error; the tag index and ECS keep them)",
            stale,
        ),
    ):
        print(f"\n{title}: {len(rows)}")
        counts: dict[str, int] = {}
        for cat, _ in rows:
            counts[cat] = counts.get(cat, 0) + 1
        for cat, n in sorted(counts.items()):
            print(f"  {cat}: {n}")
        if rows is not stale:
            for cat, item in rows[:60]:
                print(f"    - {cat}: {item}")
    if skipped:
        print(f"\nSKIPPED (no permission, not verified): {', '.join(skipped)}")
    print(
        "\nNot covered: Terraform state bucket objects (kept on purpose), CloudFormation artifact bucket (managed by SAM)."
    )
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
