"""Normalized inventory of one environment, to prove that Terraform and CloudFormation build the same thing.

It lists every resource tagged with the repository and the environment (Resource Groups Tagging API),
adds the security group rules of the project groups (rules created by CloudFormation carry no tags) and a
few selected properties, removes what differs by nature between two deployments (IDs, ARNs suffixes,
account ID, aws:* and lambda:createdBy tags) and writes a sorted JSON file.

Read-only (tagging get-resources, ec2 describe-security-groups and describe-security-group-rules, lambda
get-function-configuration, logs describe-log-groups). Standard library plus the AWS CLI.

Usage (after each deployment, same environment):

    python scripts/iac_inventory.py --env dev --output inventory-terraform-dev.json
    # ... destroy with Terraform, switch IAC_TOOL, deploy with CloudFormation ...
    python scripts/iac_inventory.py --env dev --output inventory-cloudformation-dev.json
    git diff --no-index inventory-terraform-dev.json inventory-cloudformation-dev.json

Keep the output files outside the repository (they hold resource names). Use --profile for local runs.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_NAME = "over-engineered-simple-calculator-aws-ecs-version"
IGNORED_TAG_PREFIXES = ("aws:", "lambda:createdBy")
# Runtime artifacts that come and go (running tasks and their network interfaces, automated snapshots) and
# security group rules, which are compared in their own section (CloudFormation rules carry no tags).
SKIPPED_TYPES = {"ecs:task", "ec2:network-interface", "rds:snapshot", "ec2:security-group-rule"}
# Physical IDs that differ between two deployments of the same template.
ID_PATTERNS = [
    (re.compile(r"\b\d{12}\b"), "<account>"),
    (re.compile(r"\b(vpc|subnet|rtb|igw|sg|sgr|eipalloc|vpce|nat|eni|fl|acl)-[0-9a-f]{8,17}\b"), r"\1-<id>"),
    (
        re.compile(r"(loadbalancer/app/[^/]+/|targetgroup/[^/]+/|listener/app/[^/]+/)[0-9a-f]{16}(/[0-9a-f]{16})*"),
        r"\1<id>",
    ),
    (re.compile(r"(listener-rule/app/[^/]+/)[0-9a-f/]+"), r"\1<id>"),
    (re.compile(r"(:secret:[^:]+)-[A-Za-z0-9]{6}$"), r"\1-<suffix>"),
    (re.compile(r"rds!db-[0-9a-f-]{36}"), "rds!db-<uuid>"),
    (re.compile(r"(key/)[0-9a-f-]{36}"), r"\1<id>"),
    (re.compile(r"(task-definition/[^:]+):\d+"), r"\1:<revision>"),
    (re.compile(r"(cluster/[^/]+/)[0-9a-f]{32}"), r"\1<id>"),
]


def aws(args: list[str], region: str, profile: str | None) -> Any:
    command = ["aws", *args, "--region", region, "--output", "json"]
    if profile:
        command += ["--profile", profile]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"AWS CLI call failed ({' '.join(args[:2])}): {completed.stderr.strip()[:500]}")
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def normalize(value: str) -> str:
    for pattern, replacement in ID_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def resource_type(arn: str) -> str:
    """service:resource-type from an ARN (arn:partition:service:region:account:type/... or type:...)."""
    parts = arn.split(":", 5)
    service, rest = parts[2], parts[5]
    kind = re.split(r"[/:]", rest, maxsplit=1)[0]
    return f"{service}:{kind}" if kind != rest else f"{service}:{service}"


def tagged_resources(env: str, region: str, profile: str | None) -> list[dict[str, Any]]:
    data = aws(
        [
            "resourcegroupstaggingapi",
            "get-resources",
            "--tag-filters",
            f"Key=repo-name,Values={REPO_NAME}",
            f"Key=env-type,Values={env}",
        ],
        region,
        profile,
    )
    items = []
    for mapping in data.get("ResourceTagMappingList", []):
        arn = mapping["ResourceARN"]
        tags = {t["Key"]: t["Value"] for t in mapping.get("Tags", []) if not t["Key"].startswith(IGNORED_TAG_PREFIXES)}
        items.append(
            {
                "arn": arn,
                "type": resource_type(arn),
                "name": tags.get("Name") or normalize(arn.split(":", 5)[5]),
                "tags": tags,
            }
        )
    return items


def security_group_rules(env: str, region: str, profile: str | None) -> list[dict[str, Any]]:
    groups = aws(
        [
            "ec2",
            "describe-security-groups",
            "--filters",
            f"Name=tag:repo-name,Values={REPO_NAME}",
            f"Name=tag:env-type,Values={env}",
        ],
        region,
        profile,
    ).get("SecurityGroups", [])
    names = {g["GroupId"]: g.get("GroupName", "") for g in groups}
    if not names:
        return []
    rules = aws(
        ["ec2", "describe-security-group-rules", "--filters", f"Name=group-id,Values={','.join(names)}"],
        region,
        profile,
    ).get("SecurityGroupRules", [])
    result = []
    for rule in rules:
        peer = (
            rule.get("CidrIpv4")
            or rule.get("CidrIpv6")
            or names.get((rule.get("ReferencedGroupInfo") or {}).get("GroupId", ""), "other-group")
        )
        result.append(
            {
                "group": names[rule["GroupId"]],
                "direction": "egress" if rule["IsEgress"] else "ingress",
                "protocol": rule.get("IpProtocol"),
                "ports": f"{rule.get('FromPort')}-{rule.get('ToPort')}",
                "peer": peer,
                "description": rule.get("Description", ""),
            }
        )
    return sorted(result, key=lambda r: json.dumps(r, sort_keys=True))


def selected_properties(item: dict[str, Any], region: str, profile: str | None) -> dict[str, Any]:
    """A few properties per type, enough to catch a different setting with the same name."""
    arn = item["arn"]
    if item["type"] == "lambda:function":
        conf = aws(["lambda", "get-function-configuration", "--function-name", arn], region, profile)
        return {key: conf.get(key) for key in ("Runtime", "Handler", "MemorySize", "Timeout", "Architectures")} | {
            "TracingMode": (conf.get("TracingConfig") or {}).get("Mode"),
            "LogFormat": (conf.get("LoggingConfig") or {}).get("LogFormat"),
            "EnvironmentKeys": sorted(((conf.get("Environment") or {}).get("Variables") or {}).keys()),
            "InVpc": bool((conf.get("VpcConfig") or {}).get("SubnetIds")),
        }
    if item["type"] == "logs:log-group":
        name = arn.split(":log-group:", 1)[1].removesuffix(":*")
        groups = aws(["logs", "describe-log-groups", "--log-group-name-prefix", name], region, profile)
        for group in groups.get("logGroups", []):
            if group["logGroupName"] == name:
                return {"retentionInDays": group.get("retentionInDays"), "encrypted": bool(group.get("kmsKeyId"))}
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", required=True, choices=["dev", "prod"])
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    resources = []
    seen: set[tuple[str, str]] = set()
    for item in tagged_resources(args.env, args.region, args.profile):
        # Task definition revisions collapse into one entry per family; other same-name entries are kept.
        key = (item["type"], normalize(item["name"]))
        if item["type"] in SKIPPED_TYPES or (item["type"] == "ecs:task-definition" and key in seen):
            continue
        seen.add(key)
        entry = {
            "type": item["type"],
            "name": item["name"],
            "tags": dict(sorted(item["tags"].items())),
            "properties": selected_properties(item, args.region, args.profile),
        }
        resources.append(entry)
    resources.sort(key=lambda r: (r["type"], r["name"]))
    counts: dict[str, int] = {}
    for entry in resources:
        counts[entry["type"]] = counts.get(entry["type"], 0) + 1
    inventory = {
        "environment": args.env,
        "repository": REPO_NAME,
        "counts": dict(sorted(counts.items())),
        "resources": resources,
        "securityGroupRules": security_group_rules(args.env, args.region, args.profile),
    }
    text = normalize(json.dumps(inventory, indent=2, sort_keys=False)) + "\n"
    args.output.write_bytes(text.encode("utf-8"))
    print(
        f"{len(resources)} tagged resources, {len(inventory['securityGroupRules'])} security group rules"
        f" -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
