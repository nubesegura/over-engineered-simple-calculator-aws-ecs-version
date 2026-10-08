"""Tests of scripts/iac_owner_guard.py with faked AWS CLI responses (no AWS access).

`check`, nine cases: owner none, terraform or cloudformation, each with the requested tool terraform
or cloudformation (six), plus both tools own resources, CloudFormation unreadable and Terraform state
unreadable. `detect`, five outputs: none, terraform, cloudformation, both and unreadable.
Run with: python -m unittest discover -s scripts/tests  (or pytest scripts/tests).
"""

from __future__ import annotations

import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import iac_owner_guard as guard  # noqa: E402

ACCOUNT = "111122223333"
STACKS_FILE = Path(__file__).resolve().parents[2] / "templates" / "stacks.json"

STATE_WITH_RESOURCES = json.dumps(
    {
        "version": 4,
        "resources": [
            {"mode": "data", "type": "aws_region", "instances": [{}]},
            {"mode": "managed", "type": "aws_vpc", "instances": [{"attributes": {"secret": "never printed"}}]},
        ],
    }
)
STATE_EMPTY = json.dumps({"version": 4, "resources": []})


class FakeAws:
    """Answers the CLI calls of the guard from a small in-memory description of the account."""

    def __init__(
        self,
        stacks: dict[str, str] | None = None,
        states: dict[str, str] | None = None,
        cfn_error: bool = False,
        s3_error: bool = False,
    ) -> None:
        self.stacks = stacks or {}
        self.states = states or {}
        self.cfn_error = cfn_error
        self.s3_error = s3_error
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], region: str, profile: str | None) -> guard.CliResult:
        self.calls.append(args)
        if args[:2] == ["sts", "get-caller-identity"]:
            return guard.CliResult(0, ACCOUNT + "\n", "")
        if args[:2] == ["cloudformation", "describe-stacks"]:
            if self.cfn_error:
                return guard.CliResult(254, "", "An error occurred (AccessDenied) when calling DescribeStacks")
            name = args[args.index("--stack-name") + 1]
            if name not in self.stacks:
                return guard.CliResult(
                    254, "", f"An error occurred (ValidationError): Stack with id {name} does not exist"
                )
            return guard.CliResult(0, json.dumps({"Stacks": [{"StackStatus": self.stacks[name]}]}), "")
        if args[:2] == ["s3api", "head-object"]:
            if self.s3_error:
                return guard.CliResult(
                    254, "", "An error occurred (403) when calling the HeadObject operation: Forbidden"
                )
            key = args[args.index("--key") + 1]
            unit = key.split("/")[-2]
            if unit not in self.states:
                return guard.CliResult(
                    254, "", "An error occurred (404) when calling the HeadObject operation: Not Found"
                )
            return guard.CliResult(0, "{}", "")
        if args[:2] == ["s3api", "get-object"]:
            key = args[args.index("--key") + 1]
            unit = key.split("/")[-2]
            outfile = Path(args[args.index("--key") + 2])
            outfile.write_text(self.states[unit], encoding="utf-8")
            return guard.CliResult(0, "{}", "")
        raise AssertionError(f"unexpected AWS CLI call: {args}")


def run_guard(fake: FakeAws, tool: str, env: str = "dev") -> tuple[int, str]:
    out = StringIO()
    with mock.patch.object(guard, "run_aws", fake), redirect_stdout(out):
        code = guard.main(["check", "--tool", tool, "--env", env, "--stacks-file", str(STACKS_FILE)])
    return code, out.getvalue()


def cfn_owned() -> FakeAws:
    return FakeAws(stacks={"stk-useast2-oecalc-security-dev": "ROLLBACK_COMPLETE"})


def tf_owned() -> FakeAws:
    return FakeAws(states={"network": STATE_WITH_RESOURCES, "security": STATE_EMPTY})


class OwnerGuardNineCases(unittest.TestCase):
    def test_01_owner_none_requested_terraform(self) -> None:
        code, _ = run_guard(FakeAws(states={"security": STATE_EMPTY}), "terraform")
        self.assertEqual(code, guard.EXIT_ALLOWED)

    def test_02_owner_none_requested_cloudformation(self) -> None:
        code, _ = run_guard(FakeAws(), "cloudformation")
        self.assertEqual(code, guard.EXIT_ALLOWED)

    def test_03_owner_terraform_requested_terraform(self) -> None:
        code, out = run_guard(tf_owned(), "terraform")
        self.assertEqual(code, guard.EXIT_ALLOWED)
        self.assertIn("network: 1 resources", out)
        self.assertNotIn("never printed", out)

    def test_04_owner_terraform_requested_cloudformation(self) -> None:
        code, out = run_guard(tf_owned(), "cloudformation")
        self.assertEqual(code, guard.EXIT_OTHER_TOOL)
        self.assertIn("deployed with terraform", out)
        self.assertIn("Destroy it with that tool", out)

    def test_05_owner_cloudformation_requested_cloudformation(self) -> None:
        code, out = run_guard(cfn_owned(), "cloudformation")
        self.assertEqual(code, guard.EXIT_ALLOWED)
        self.assertIn("stk-useast2-oecalc-security-dev (ROLLBACK_COMPLETE)", out)

    def test_06_owner_cloudformation_requested_terraform(self) -> None:
        code, out = run_guard(cfn_owned(), "terraform")
        self.assertEqual(code, guard.EXIT_OTHER_TOOL)
        self.assertIn("deployed with cloudformation", out)

    def test_07_both_tools_own_resources(self) -> None:
        fake = FakeAws(
            stacks={"stk-useast2-oecalc-network-dev": "UPDATE_IN_PROGRESS"},
            states={"database": STATE_WITH_RESOURCES},
        )
        code, _ = run_guard(fake, "terraform")
        self.assertEqual(code, guard.EXIT_BOTH_TOOLS)

    def test_08_cloudformation_unreadable_fails_closed(self) -> None:
        code, out = run_guard(FakeAws(cfn_error=True), "cloudformation")
        self.assertEqual(code, guard.EXIT_UNREADABLE)
        self.assertIn("Failing closed", out)

    def test_09_terraform_state_unreadable_fails_closed(self) -> None:
        code, _ = run_guard(FakeAws(s3_error=True), "terraform")
        self.assertEqual(code, guard.EXIT_UNREADABLE)


def run_detect(fake: FakeAws, env: str = "dev") -> tuple[int, str]:
    out, err = StringIO(), StringIO()
    with mock.patch.object(guard, "run_aws", fake), redirect_stdout(out), redirect_stderr(err):
        code = guard.main(["detect", "--env", env, "--stacks-file", str(STACKS_FILE)])
    return code, out.getvalue()


class OwnerGuardDetectFiveOutputs(unittest.TestCase):
    def test_d1_none(self) -> None:
        self.assertEqual(run_detect(FakeAws(states={"security": STATE_EMPTY})), (0, "owner=none\n"))

    def test_d2_terraform(self) -> None:
        self.assertEqual(run_detect(tf_owned()), (0, "owner=terraform\n"))

    def test_d3_cloudformation(self) -> None:
        self.assertEqual(run_detect(cfn_owned()), (0, "owner=cloudformation\n"))

    def test_d4_both(self) -> None:
        fake = FakeAws(
            stacks={"stk-useast2-oecalc-alarms-dev": "DELETE_FAILED"}, states={"alarms": STATE_WITH_RESOURCES}
        )
        self.assertEqual(run_detect(fake), (guard.EXIT_BOTH_TOOLS, "owner=both\n"))

    def test_d5_unreadable_prints_no_owner(self) -> None:
        self.assertEqual(run_detect(FakeAws(s3_error=True)), (guard.EXIT_UNREADABLE, ""))
        self.assertEqual(run_detect(FakeAws(cfn_error=True)), (guard.EXIT_UNREADABLE, ""))


class OwnerGuardEdgeCases(unittest.TestCase):
    def test_malformed_state_fails_closed(self) -> None:
        code, _ = run_guard(FakeAws(states={"network": "not json"}), "terraform")
        self.assertEqual(code, guard.EXIT_UNREADABLE)

    def test_malformed_stack_response_fails_closed(self) -> None:
        fake = FakeAws(stacks={"stk-useast2-oecalc-network-dev": "x"})
        original = fake.__call__

        def broken(args: list[str], region: str, profile: str | None) -> guard.CliResult:
            if args[:2] == ["cloudformation", "describe-stacks"] and "network" in args[args.index("--stack-name") + 1]:
                return guard.CliResult(0, "<html>", "")
            return original(args, region, profile)

        code, _ = run_guard(broken, "terraform")  # type: ignore[arg-type]
        self.assertEqual(code, guard.EXIT_UNREADABLE)

    def test_invalid_tool_fails_closed(self) -> None:
        with self.assertRaises(SystemExit) as raised, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            guard.main(["check", "--tool", "pulumi", "--env", "dev", "--stacks-file", str(STACKS_FILE)])
        self.assertEqual(raised.exception.code, guard.EXIT_UNREADABLE)

    def test_prod_message_mentions_no_switch(self) -> None:
        code, message = guard.decide("cloudformation", "prod", False, True)
        self.assertEqual(code, guard.EXIT_OTHER_TOOL)
        self.assertIn("not supported", message)


if __name__ == "__main__":
    unittest.main()
