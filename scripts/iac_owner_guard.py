"""Owner guard: one IaC tool per environment, fail closed.

The same infrastructure can be deployed with Terraform (Terragrunt) or with CloudFormation (SAM), with
the same resource names, so the two tools must never manage one environment at the same time. It
detects which tool owns the environment from what exists (no marker tag):

* CloudFormation owns it when any stack of templates/stacks.json for the environment exists in any
  status except DELETE_COMPLETE (ROLLBACK_COMPLETE, REVIEW_IN_PROGRESS, DELETE_FAILED and any
  *_IN_PROGRESS count as present).
* Terraform owns it when the state object of any Terragrunt unit exists and holds at least one managed
  resource. The state is read only to count resources; its content is never printed or kept.

Subcommands:

    check --tool T --env E   used before a deploy or a plan with tool T (the GitHub variable IAC_TOOL)
        0  the environment is free or already owned by T
        2  the environment is owned by the other tool
        3  both tools own resources (inconsistent; fix by hand)
        4  ownership could not be determined (permissions, network, malformed response, bad arguments)

    detect --env E           used by the destroy workflow, which removes what exists with the tool that
                             created it, whatever IAC_TOOL says. Prints one line on stdout:
                             owner=terraform|cloudformation|none|both
        0  owner terraform, cloudformation or none
        3  both
        4  ownership could not be determined (nothing is printed on stdout; fail closed)

Read-only: it calls the AWS CLI (sts get-caller-identity, cloudformation describe-stacks,
s3api head-object and get-object). Standard library only.

Usage:

    python scripts/iac_owner_guard.py check --tool terraform --env dev
    python scripts/iac_owner_guard.py detect --env dev --profile <profile>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

TOOLS = ("terraform", "cloudformation")
ENVIRONMENTS = ("dev", "prod")

EXIT_ALLOWED = 0
EXIT_OTHER_TOOL = 2
EXIT_BOTH_TOOLS = 3
EXIT_UNREADABLE = 4

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STACKS_FILE = REPO_ROOT / "templates" / "stacks.json"


class GuardError(Exception):
    """Ownership cannot be determined; the guard fails closed."""


class _ArgumentParser(argparse.ArgumentParser):
    """Argument errors also fail closed with the 'unreadable' exit code."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"iac_owner_guard: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_UNREADABLE)


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str


def run_aws(args: list[str], region: str, profile: str | None) -> CliResult:
    """Run one read-only AWS CLI command. Replaced by a fake in the tests."""
    command = ["aws", *args, "--region", region]
    if profile:
        command += ["--profile", profile]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CliResult(255, "", f"could not run the AWS CLI: {exc}")
    return CliResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class Config:
    region: str
    repository: str
    bucket_pattern: str
    key_pattern: str
    stacks: list[str]
    units: list[str]


def load_config(stacks_file: Path, env: str) -> Config:
    try:
        data = json.loads(stacks_file.read_text(encoding="utf-8"))
        entries = data["environments"][env]
        return Config(
            region=data["region"],
            repository=data["repository"],
            bucket_pattern=data["terraformStateBucketPattern"],
            key_pattern=data["terraformStateKeyPattern"],
            stacks=[entry["stack"] for entry in entries],
            units=[entry["unit"] for entry in entries],
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GuardError(f"cannot read {stacks_file.name}: {exc}") from exc


def _not_found(stderr: str) -> bool:
    text = stderr.lower()
    return "does not exist" in text or "not found" in text or "(404)" in text or "nosuchkey" in text


def cloudformation_stacks_present(config: Config, profile: str | None) -> list[tuple[str, str]]:
    """Return (stack name, status) of every stack of the environment that exists."""
    present: list[tuple[str, str]] = []
    for stack in config.stacks:
        result = run_aws(
            ["cloudformation", "describe-stacks", "--stack-name", stack, "--output", "json"],
            config.region,
            profile,
        )
        if result.returncode != 0:
            if "does not exist" in result.stderr:
                continue
            raise GuardError(f"cannot describe stack {stack} (exit {result.returncode})")
        try:
            stacks = json.loads(result.stdout)["Stacks"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GuardError(f"malformed describe-stacks response for {stack}") from exc
        for item in stacks:
            status = item.get("StackStatus") if isinstance(item, dict) else None
            if not isinstance(status, str):
                raise GuardError(f"malformed describe-stacks response for {stack}")
            if status != "DELETE_COMPLETE":
                present.append((stack, status))
    return present


def _account_id(config: Config, profile: str | None) -> str:
    result = run_aws(["sts", "get-caller-identity", "--query", "Account", "--output", "text"], config.region, profile)
    account = result.stdout.strip()
    if result.returncode != 0 or not (account.isdigit() and len(account) == 12):
        raise GuardError("cannot read the caller account (sts get-caller-identity)")
    return account


def _count_managed(state_text: str) -> int:
    state = json.loads(state_text)
    resources = state["resources"]
    if not isinstance(resources, list):
        raise ValueError("resources is not a list")
    count = 0
    for resource in resources:
        if resource.get("mode") == "managed":
            count += len(resource.get("instances", []))
    return count


def terraform_units_with_resources(config: Config, env: str, profile: str | None) -> list[tuple[str, int]]:
    """Return (unit, managed resource count) of every unit whose state holds resources."""
    account = _account_id(config, profile)
    bucket = config.bucket_pattern.format(region_code=config.region.replace("-", ""), env=env, account_id=account)
    owned: list[tuple[str, int]] = []
    with tempfile.TemporaryDirectory() as scratch:
        for unit in config.units:
            key = config.key_pattern.format(repository=config.repository, env=env, unit=unit)
            head = run_aws(
                ["s3api", "head-object", "--bucket", bucket, "--key", key, "--output", "json"],
                config.region,
                profile,
            )
            if head.returncode != 0:
                if _not_found(head.stderr):
                    continue
                raise GuardError(f"cannot read the state object of unit {unit} (exit {head.returncode})")
            target = Path(scratch) / f"{unit}.tfstate"
            get = run_aws(
                ["s3api", "get-object", "--bucket", bucket, "--key", key, str(target), "--output", "json"],
                config.region,
                profile,
            )
            if get.returncode != 0:
                raise GuardError(f"cannot read the state object of unit {unit} (exit {get.returncode})")
            try:
                count = _count_managed(target.read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                raise GuardError(f"malformed state object for unit {unit}") from exc
            finally:
                target.unlink(missing_ok=True)
            if count > 0:
                owned.append((unit, count))
    return owned


def decide(tool: str, env: str, cfn_present: bool, tf_present: bool) -> tuple[int, str]:
    """Map the detected owner to an exit code and a message."""
    if cfn_present and tf_present:
        return (
            EXIT_BOTH_TOOLS,
            f"The {env} environment has resources from both Terraform and CloudFormation: "
            "inconsistent state, fix it by hand before any deployment.",
        )
    owner = "cloudformation" if cfn_present else "terraform" if tf_present else "none"
    if owner in ("none", tool):
        return EXIT_ALLOWED, f"Owner of {env}: {owner}. Requested tool: {tool}. Allowed."
    message = (
        f"The {env} environment is deployed with {owner}. Destroy it with that tool "
        f"(Destroy DEV detects the owner and uses {owner}) before switching to {tool}."
    )
    if env == "prod":
        message += " Switching prod is not supported without a data migration plan."
    return EXIT_OTHER_TOOL, message


def detect_owner(stacks: list[tuple[str, str]], units: list[tuple[str, int]]) -> str:
    if stacks and units:
        return "both"
    return "cloudformation" if stacks else "terraform" if units else "none"


def _inspect(env: str, stacks_file: Path, profile: str | None) -> tuple[list[tuple[str, str]], list[tuple[str, int]]]:
    """Read both sources; GuardError when either cannot be read."""
    config = load_config(stacks_file, env)
    return cloudformation_stacks_present(config, profile), terraform_units_with_resources(config, env, profile)


def _report(env: str, stacks: list[tuple[str, str]], units: list[tuple[str, int]], stream: TextIO) -> None:
    print(f"CloudFormation stacks present in {env}: {len(stacks)}", file=stream)
    for name, status in stacks:
        print(f"  {name} ({status})", file=stream)
    print(f"Terraform units with resources in {env}: {len(units)}", file=stream)
    for unit, count in units:
        print(f"  {unit}: {count} resources", file=stream)


def command_check(args: argparse.Namespace) -> int:
    try:
        stacks, units = _inspect(args.env, args.stacks_file, args.profile)
    except GuardError as exc:
        print(f"::error::Owner guard cannot determine the owner of {args.env}: {exc}. Failing closed.")
        return EXIT_UNREADABLE
    _report(args.env, stacks, units, sys.stdout)
    code, message = decide(args.tool, args.env, bool(stacks), bool(units))
    print(message if code == EXIT_ALLOWED else f"::error::{message}")
    return code


def command_detect(args: argparse.Namespace) -> int:
    """Print owner=<...> on stdout (details on stderr), for the destroy workflow."""
    try:
        stacks, units = _inspect(args.env, args.stacks_file, args.profile)
    except GuardError as exc:
        print(f"::error::Owner guard cannot determine the owner of {args.env}: {exc}. Failing closed.", file=sys.stderr)
        return EXIT_UNREADABLE
    _report(args.env, stacks, units, sys.stderr)
    owner = detect_owner(stacks, units)
    print(f"owner={owner}")
    if owner == "both":
        print(
            f"::error::The {args.env} environment has resources from both Terraform and CloudFormation: "
            "inconsistent state, fix it by hand.",
            file=sys.stderr,
        )
        return EXIT_BOTH_TOOLS
    return EXIT_ALLOWED


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_ArgumentParser)
    check = sub.add_parser("check", help="free or owned by --tool passes")
    check.add_argument("--tool", required=True, choices=TOOLS)
    detect = sub.add_parser("detect", help="print owner=terraform|cloudformation|none|both")
    for command in (check, detect):
        command.add_argument("--env", required=True, choices=ENVIRONMENTS)
        command.add_argument("--stacks-file", type=Path, default=DEFAULT_STACKS_FILE)
        command.add_argument("--profile", default=None, help="AWS CLI profile (local runs only)")
    check.set_defaults(handler=command_check)
    detect.set_defaults(handler=command_detect)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
