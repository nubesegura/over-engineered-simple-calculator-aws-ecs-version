"""Tests of the offline parts of scripts/cfn_deploy.py: change set guard and parameter resolution."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cfn_deploy as deploy  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ALLOWED = deploy.REPLACEMENT_ALLOWED_TYPES


def change(action: str, resource_type: str, replacement: str | None = None) -> dict[str, object]:
    detail: dict[str, object] = {"Action": action, "ResourceType": resource_type, "LogicalResourceId": "X"}
    if replacement is not None:
        detail["Replacement"] = replacement
    return {"ResourceChange": detail}


class ChangeSetGuard(unittest.TestCase):
    def test_add_and_in_place_modify_pass(self) -> None:
        changes = [change("Add", "AWS::S3::Bucket"), change("Modify", "AWS::Lambda::Function", "False")]
        self.assertEqual(deploy.guard_changes(changes, ALLOWED), [])

    def test_remove_is_refused(self) -> None:
        self.assertEqual(len(deploy.guard_changes([change("Remove", "AWS::S3::Bucket")], ALLOWED)), 1)

    def test_replacement_is_refused(self) -> None:
        self.assertEqual(len(deploy.guard_changes([change("Modify", "AWS::RDS::DBInstance", "True")], ALLOWED)), 1)

    def test_task_definition_replacement_is_allowed(self) -> None:
        self.assertEqual(deploy.guard_changes([change("Modify", "AWS::ECS::TaskDefinition", "True")], ALLOWED), [])

    def test_conditional_replacement_is_refused_for_stateful_and_edge_types(self) -> None:
        for resource_type in (
            "AWS::RDS::DBInstance",
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            "AWS::KMS::Key",
            "AWS::S3::Bucket",
            "AWS::EC2::SecurityGroup",
        ):
            with self.subTest(resource_type=resource_type):
                self.assertEqual(
                    len(deploy.guard_changes([change("Modify", resource_type, "Conditional")], ALLOWED)), 1
                )

    def test_conditional_replacement_of_allowlisted_type_passes(self) -> None:
        self.assertEqual(
            deploy.guard_changes([change("Modify", "AWS::ECS::TaskDefinition", "Conditional")], ALLOWED), []
        )

    def test_allowlist_is_only_task_definitions(self) -> None:
        self.assertEqual(deploy.REPLACEMENT_ALLOWED_TYPES, frozenset({"AWS::ECS::TaskDefinition"}))


class ChangeSetName(unittest.TestCase):
    def test_rerun_of_same_run_id_does_not_collide(self) -> None:
        first = deploy.change_set_name("123", "1", "network")
        second = deploy.change_set_name("123", "2", "network")
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^[a-zA-Z][-a-zA-Z0-9]*$")

    def test_unsafe_characters_are_replaced(self) -> None:
        self.assertRegex(deploy.change_set_name("1_2", "x y", "edge"), r"^[a-zA-Z][-a-zA-Z0-9]*$")


class EmptyBucket(unittest.TestCase):
    def test_delete_errors_stop_the_job(self) -> None:
        page = {"Versions": [{"Key": "a.csv", "VersionId": "v1"}]}
        errors = {"Errors": [{"Key": "a.csv", "VersionId": "v1", "Code": "AccessDenied", "Message": "denied"}]}
        with mock.patch.object(deploy, "aws_json", side_effect=[page, errors]):
            with self.assertRaises(deploy.DeployError) as raised:
                deploy.empty_bucket("bucket")
        self.assertIn("AccessDenied", str(raised.exception))

    def test_empties_until_no_versions_left(self) -> None:
        pages = [
            {"Versions": [{"Key": "a", "VersionId": "1"}], "DeleteMarkers": [{"Key": "a", "VersionId": "2"}]},
            {},
            {},
        ]
        with mock.patch.object(deploy, "aws_json", side_effect=pages):
            self.assertEqual(deploy.empty_bucket("bucket"), 2)


class Parameters(unittest.TestCase):
    def test_every_template_resolves_for_both_environments(self) -> None:
        github_values = {
            "IMAGE_TAG": "0123abc",
            "SUPPORT_EMAIL": "alerts@example.com",
            "COGNITO_USER_POOL_ID": "pool",
            "COGNITO_APP_CLIENT_ID": "client",
            "WAF_ACL_ARN": "",
        }
        with mock.patch.dict(os.environ, github_values):
            for env in ("dev", "prod"):
                _, entries = deploy.load_stacks(env)
                for entry in entries:
                    params = deploy.stack_parameters(env, REPO / entry["template"])
                    keys = {p["ParameterKey"] for p in params}
                    self.assertIn("EnvType", keys, entry["template"])
                    self.assertEqual(next(p["ParameterValue"] for p in params if p["ParameterKey"] == "EnvType"), env)

    def test_parameter_file_of_another_environment_is_refused(self) -> None:
        source = json.loads((REPO / "parameters" / "prod.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "parameters").mkdir()
            (Path(root) / "parameters" / "dev.json").write_text(json.dumps(source), encoding="utf-8")
            with mock.patch.object(deploy, "REPO_ROOT", Path(root)), self.assertRaises(deploy.DeployError) as raised:
                deploy.load_parameters("dev")
        self.assertIn("EnvType", str(raised.exception))

    def test_both_parameter_files_match_their_environment(self) -> None:
        for env in ("dev", "prod"):
            self.assertEqual(deploy.load_parameters(env)["Parameters"]["EnvType"], env)

    def test_missing_github_value_is_reported(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(deploy.DeployError):
            deploy.stack_parameters("dev", REPO / "templates" / "cluster.yaml")


if __name__ == "__main__":
    unittest.main()
