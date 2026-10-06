"""Tests of the NAT scheduler with moto EC2."""

import json
import time
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from nat_scheduler import handler as h

TAGS = {"Project": "oecalc", "Environment": "dev", "Owner": "me", "CostCenter": "x", "Tier": "y"}


class Env:
    """Network fixture: one subnet, one private route table and one Elastic IP."""

    def __init__(self) -> None:
        self.ec2: Any = boto3.client("ec2", region_name="us-east-1")
        vpc = self.ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        subnet = self.ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")
        self.subnet: str = subnet["Subnet"]["SubnetId"]
        self.table: str = self.ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
        self.eip: str = self.ec2.allocate_address(Domain="vpc")["AllocationId"]

    def gateways(self) -> list[Any]:
        found = self.ec2.describe_nat_gateways()["NatGateways"]
        return [g for g in found if g["State"] in ("pending", "available")]

    def default_target(self) -> str | None:
        tables = self.ec2.describe_route_tables(RouteTableIds=[self.table])["RouteTables"]
        for route in tables[0]["Routes"]:
            if route.get("DestinationCidrBlock") == "0.0.0.0/0":
                return str(route.get("NatGatewayId"))
        return None


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        e = Env()
        monkeypatch.setenv("PUBLIC_SUBNET_ID", e.subnet)
        monkeypatch.setenv("PRIVATE_ROUTE_TABLE_ID", e.table)
        monkeypatch.setenv("EIP_ALLOCATION_ID", e.eip)
        monkeypatch.setenv("NAME_TAG", "nat-test")
        monkeypatch.setenv("TAGS_JSON", json.dumps(TAGS))
        yield e


def run(action: str) -> dict[str, str]:
    return h.handler({"action": action}, None)


def test_on_creates_gateway_tags_it_and_routes(env: Env) -> None:
    run("on")
    (gateway,) = env.gateways()
    tags = {t["Key"]: t["Value"] for t in gateway["Tags"]}
    assert tags == {"Name": "nat-test", **TAGS}
    assert env.default_target() == gateway["NatGatewayId"]


def test_on_twice_changes_nothing(env: Env) -> None:
    run("on")
    before = env.gateways()[0]["NatGatewayId"]
    run("on")
    assert [g["NatGatewayId"] for g in env.gateways()] == [before]
    assert env.default_target() == before


def test_on_replaces_existing_default_route(env: Env) -> None:
    igw = env.ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    env.ec2.create_route(RouteTableId=env.table, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)
    run("on")
    assert env.default_target() == env.gateways()[0]["NatGatewayId"]


def test_off_removes_route_then_gateway(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    run("on")
    calls: list[str] = []
    real = boto3.client

    def spying_client(*args: Any, **kwargs: Any) -> Any:
        client = real(*args, **kwargs)
        for name in ("delete_route", "delete_nat_gateway"):
            original = getattr(client, name)

            def wrapper(*a: Any, _n: str = name, _o: Any = original, **k: Any) -> Any:
                calls.append(_n)
                return _o(*a, **k)

            setattr(client, name, wrapper)
        return client

    monkeypatch.setattr(boto3, "client", spying_client)
    run("off")
    assert calls == ["delete_route", "delete_nat_gateway"]
    assert env.default_target() is None
    assert env.gateways() == []


def test_off_twice_changes_nothing(env: Env) -> None:
    run("on")
    run("off")
    assert run("off") == {"action": "off", "status": "ok"}
    assert env.gateways() == []


def test_off_keeps_route_of_foreign_gateway(env: Env) -> None:
    other = env.ec2.create_nat_gateway(SubnetId=env.subnet, AllocationId=env.eip)["NatGateway"]
    other_id = other["NatGatewayId"]
    env.ec2.create_route(
        RouteTableId=env.table, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=other_id
    )
    run("off")
    assert env.default_target() == other_id
    assert len(env.gateways()) == 1


def test_pending_gateway_is_reused_after_waiting(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    run("on")
    env.ec2.delete_route(RouteTableId=env.table, DestinationCidrBlock="0.0.0.0/0")
    waited: list[str] = []
    monkeypatch.setattr(h, "_wait_available", lambda _ec2, gid: waited.append(gid))
    run("on")
    gateway_id = env.gateways()[0]["NatGatewayId"]
    assert waited == [gateway_id]
    assert len(env.gateways()) == 1
    assert env.default_target() == gateway_id


def test_on_refuses_while_gateway_is_deleting(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(_ec2: Any, _s: h.Settings, states: list[str]) -> list[dict[str, Any]]:
        return [{"NatGatewayId": "nat-1", "State": "deleting"}] if states == ["deleting"] else []

    monkeypatch.setattr(h, "_module_gateways", fake)
    with pytest.raises(h.SchedulerError, match="still deleting"):
        run("on")


def test_off_ignores_gateway_already_deleting(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[str] = []

    class FakeEc2:
        def describe_nat_gateways(self, **_k: Any) -> dict[str, Any]:
            return {"NatGateways": [{"NatGatewayId": "nat-1", "State": "deleting"}]}

        def describe_route_tables(self, **_k: Any) -> dict[str, Any]:
            return {"RouteTables": [{"Routes": []}]}

        def delete_nat_gateway(self, NatGatewayId: str) -> None:  # noqa: N803
            deleted.append(NatGatewayId)

    h.action_off(FakeEc2(), h.load_settings())
    assert deleted == []


def test_recovery_when_wait_fails_between_steps(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    real_wait = h._wait_available

    def boom(_ec2: Any, _gid: str) -> None:
        raise RuntimeError("waiter timed out")

    monkeypatch.setattr(h, "_wait_available", boom)
    with pytest.raises(RuntimeError):
        run("on")
    assert env.default_target() is None
    assert len(env.gateways()) == 1
    monkeypatch.setattr(h, "_wait_available", real_wait)
    run("on")
    assert len(env.gateways()) == 1
    assert env.default_target() == env.gateways()[0]["NatGatewayId"]


def test_off_recovers_when_route_is_already_gone(env: Env) -> None:
    run("on")
    env.ec2.delete_route(RouteTableId=env.table, DestinationCidrBlock="0.0.0.0/0")
    run("off")
    assert env.gateways() == []


def test_invalid_action_and_missing_settings_raise(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(h.SchedulerError):
        run("restart")
    monkeypatch.delenv("NAME_TAG")
    with pytest.raises(h.SchedulerError, match="Missing"):
        run("on")


def test_terraform_delete_runs_off_even_when_action_says_on(env: Env) -> None:
    run("on")
    result = h.handler({"action": "on", "tf": {"action": "delete"}}, None)
    assert result == {"action": "off", "status": "ok"}
    assert env.default_target() is None
    assert env.gateways() == []


def test_terraform_create_keeps_the_action(env: Env) -> None:
    h.handler({"action": "on", "tf": {"action": "create"}}, None)
    assert len(env.gateways()) == 1
    assert env.default_target() == env.gateways()[0]["NatGatewayId"]


@pytest.mark.parametrize("tf", ["delete", None, 5, ["delete"], {"action": 1}])
def test_malformed_tf_is_ignored(env: Env, tf: Any) -> None:
    assert h.handler({"action": "on", "tf": tf}, None)["action"] == "on"
    assert len(env.gateways()) == 1


def _spy_waits(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    waited: list[list[str]] = []
    monkeypatch.setattr(h, "_wait_deleted", lambda _ec2, ids: waited.append(ids))
    return waited


def test_terraform_delete_waits_for_the_gateway_deletion(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("on")
    gateway_id = env.gateways()[0]["NatGatewayId"]
    waited = _spy_waits(monkeypatch)
    h.handler({"action": "on", "tf": {"action": "delete"}}, None)
    assert waited == [[gateway_id]]


def test_nightly_off_does_not_wait(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    run("on")
    waited = _spy_waits(monkeypatch)
    run("off")
    assert waited == []


def test_repeated_terraform_delete_is_a_no_op(env: Env) -> None:
    run("on")
    event = {"action": "on", "tf": {"action": "delete"}}
    h.handler(event, None)
    assert h.handler(event, None) == {"action": "off", "status": "ok"}
    assert env.gateways() == []


def test_wait_deleted_polls_until_state_is_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    states = iter(["deleting", "deleting", "deleted"])
    sleeps: list[int] = []

    class FakeEc2:
        def describe_nat_gateways(self, **_k: Any) -> dict[str, Any]:
            return {"NatGateways": [{"State": next(states)}]}

    monkeypatch.setattr(time, "sleep", sleeps.append)
    h._wait_deleted(FakeEc2(), ["nat-1"])
    assert len(sleeps) == 2


def test_wait_deleted_times_out_with_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEc2:
        def describe_nat_gateways(self, **_k: Any) -> dict[str, Any]:
            return {"NatGateways": [{"State": "deleting"}]}

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(h.SchedulerError, match="Timed out"):
        h._wait_deleted(FakeEc2(), ["nat-1"])
