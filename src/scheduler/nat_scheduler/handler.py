"""Lambda that creates (action "on") or removes (action "off") the NAT gateway on a schedule.

Configuration comes from environment variables set by Terraform:
PUBLIC_SUBNET_ID, PRIVATE_ROUTE_TABLE_ID, EIP_ALLOCATION_ID, NAME_TAG and TAGS_JSON.

Both actions are idempotent. A failure raises after being logged so that the function error
alarm fires; the next invocation resumes from whatever state the previous one left. The private
default route never points to a gateway that is being removed: "off" deletes the route first.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

DEFAULT_CIDR = "0.0.0.0/0"
LIVE_STATES = ["pending", "available"]
ALL_STATES = ["pending", "available", "deleting"]
WAIT_DELAY_SECONDS = 5
WAIT_MAX_ATTEMPTS = 48
CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 5, "mode": "standard"},
)


class SchedulerError(Exception):
    """Raised when the requested action cannot be completed."""


@dataclass(frozen=True)
class Settings:
    """Validated configuration of the function."""

    public_subnet_id: str
    private_route_table_id: str
    eip_allocation_id: str
    name_tag: str
    tags: dict[str, str]


def load_settings() -> Settings:
    """Read and validate the environment variables, failing fast when one is missing."""
    names = [
        "PUBLIC_SUBNET_ID",
        "PRIVATE_ROUTE_TABLE_ID",
        "EIP_ALLOCATION_ID",
        "NAME_TAG",
        "TAGS_JSON",
    ]
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SchedulerError(f"Missing environment variables: {missing}")
    tags = json.loads(os.environ["TAGS_JSON"])
    if not isinstance(tags, dict):
        raise SchedulerError("TAGS_JSON must be a JSON object")
    return Settings(
        public_subnet_id=os.environ["PUBLIC_SUBNET_ID"],
        private_route_table_id=os.environ["PRIVATE_ROUTE_TABLE_ID"],
        eip_allocation_id=os.environ["EIP_ALLOCATION_ID"],
        name_tag=os.environ["NAME_TAG"],
        tags={str(k): str(v) for k, v in tags.items()},
    )


def _module_gateways(ec2: Any, settings: Settings, states: list[str]) -> list[dict[str, Any]]:
    response = ec2.describe_nat_gateways(
        Filter=[
            {"Name": "tag:Name", "Values": [settings.name_tag]},
            {"Name": "state", "Values": states},
        ]
    )
    return [g for g in response["NatGateways"] if _has_name(g, settings.name_tag)]


def _has_name(gateway: dict[str, Any], name: str) -> bool:
    tags = {t["Key"]: t["Value"] for t in gateway.get("Tags", [])}
    return tags.get("Name") == name


def _default_route(ec2: Any, route_table_id: str) -> dict[str, Any] | None:
    tables = ec2.describe_route_tables(RouteTableIds=[route_table_id])["RouteTables"]
    for route in tables[0]["Routes"]:
        if route.get("DestinationCidrBlock") == DEFAULT_CIDR:
            found: dict[str, Any] = route
            return found
    return None


def _wait_available(ec2: Any, gateway_id: str) -> None:
    waiter = ec2.get_waiter("nat_gateway_available")
    waiter.wait(
        NatGatewayIds=[gateway_id],
        WaiterConfig={"Delay": WAIT_DELAY_SECONDS, "MaxAttempts": WAIT_MAX_ATTEMPTS},
    )


def _create_gateway(ec2: Any, settings: Settings) -> str:
    if _module_gateways(ec2, settings, ["deleting"]):
        raise SchedulerError("A NAT gateway of this module is still deleting; retry later")
    tags = [{"Key": "Name", "Value": settings.name_tag}]
    tags += [{"Key": k, "Value": v} for k, v in settings.tags.items() if k != "Name"]
    response = ec2.create_nat_gateway(
        SubnetId=settings.public_subnet_id,
        AllocationId=settings.eip_allocation_id,
        TagSpecifications=[{"ResourceType": "natgateway", "Tags": tags}],
    )
    gateway_id: str = response["NatGateway"]["NatGatewayId"]
    LOGGER.info("Created NAT gateway %s", gateway_id)
    return gateway_id


def _point_route(ec2: Any, settings: Settings, gateway_id: str) -> None:
    route = _default_route(ec2, settings.private_route_table_id)
    if route and route.get("NatGatewayId") == gateway_id and route.get("State") == "active":
        return
    args = {
        "RouteTableId": settings.private_route_table_id,
        "DestinationCidrBlock": DEFAULT_CIDR,
        "NatGatewayId": gateway_id,
    }
    if route is None:
        ec2.create_route(**args)
    else:
        ec2.replace_route(**args)
    LOGGER.info("Default route of %s now targets %s", settings.private_route_table_id, gateway_id)


def action_on(ec2: Any, settings: Settings) -> None:
    """Ensure a NAT gateway exists, is available and is the private default route target."""
    live = _module_gateways(ec2, settings, LIVE_STATES)
    gateway_id = live[0]["NatGatewayId"] if live else _create_gateway(ec2, settings)
    _wait_available(ec2, gateway_id)
    _point_route(ec2, settings, gateway_id)


def _wait_deleted(ec2: Any, gateway_ids: list[str]) -> None:
    """Poll until every gateway is deleted (a missing gateway counts as deleted)."""
    for _ in range(WAIT_MAX_ATTEMPTS):
        found = ec2.describe_nat_gateways(NatGatewayIds=gateway_ids)["NatGateways"]
        if all(g["State"] == "deleted" for g in found):
            return
        time.sleep(WAIT_DELAY_SECONDS)
    raise SchedulerError("Timed out waiting for the NAT gateway deletion")


def action_off(ec2: Any, settings: Settings, wait: bool = False) -> None:
    """Remove the default route when it targets this module's gateway, then the gateway.

    With wait=True (Terraform destroy) it also waits until the gateways are deleted, so the
    Elastic IP and the subnets can be removed right after.
    """
    gateways = _module_gateways(ec2, settings, ALL_STATES)
    ids = {g["NatGatewayId"] for g in gateways}
    route = _default_route(ec2, settings.private_route_table_id)
    if route is not None and route.get("NatGatewayId") in ids:
        ec2.delete_route(
            RouteTableId=settings.private_route_table_id, DestinationCidrBlock=DEFAULT_CIDR
        )
        LOGGER.info("Deleted default route of %s", settings.private_route_table_id)
    for gateway in gateways:
        if gateway["State"] in LIVE_STATES:
            ec2.delete_nat_gateway(NatGatewayId=gateway["NatGatewayId"])
            LOGGER.info("Deleting NAT gateway %s", gateway["NatGatewayId"])
    if wait and ids:
        _wait_deleted(ec2, sorted(ids))


def _effective_action(event: dict[str, Any]) -> tuple[Any, bool]:
    """Return the action to run and whether to wait for deletion.

    A Terraform destroy (tf.action == "delete") always means off, and off then waits.
    """
    terraform = event.get("tf")
    if isinstance(terraform, dict) and terraform.get("action") == "delete":
        return "off", True
    return event.get("action"), False


def handler(event: dict[str, Any], _context: object) -> dict[str, str]:
    """Entrypoint: event {"action": "on" | "off"}, optionally with the "tf" key that the
    Terraform aws_lambda_invocation resource (CRUD scope) adds."""
    action, wait = _effective_action(event)
    try:
        if action not in ("on", "off"):
            raise SchedulerError("Event action must be 'on' or 'off'")
        settings = load_settings()
        ec2 = boto3.client("ec2", config=CLIENT_CONFIG)
        if action == "on":
            action_on(ec2, settings)
        else:
            action_off(ec2, settings, wait)
    except Exception:
        LOGGER.exception("NAT scheduler action %s failed", action)
        raise
    LOGGER.info("NAT scheduler action %s done", action)
    return {"action": str(action), "status": "ok"}
