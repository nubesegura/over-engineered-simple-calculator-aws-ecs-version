# ADR 0003: Scheduled NAT gateway

Status: Accepted (2026-10-05)

## Context

Private tasks need egress to ECR, Secrets Manager, SNS and CloudWatch Logs. A NAT gateway is the safest path but costs by the hour; VPC endpoints and public task addresses were rejected (cost, and nothing that processes data may be public).

## Decision

Keep one NAT gateway and remove it daily from 22:00 to 08:00 (`America/Panama`). EventBridge Scheduler invokes a Lambda outside the VPC that creates and deletes the NAT gateway and the private default route; the Elastic IP stays. Terraform invokes the function at apply and destroy and never owns the NAT. The pipeline turns the NAT on before it deploys.

## Consequences

Saves the hourly cost for 14 hours a day. At night nothing can pull images, ship logs, send alerts or reach Secrets Manager: new tasks, new database connections after a rotation and ingestion runs may fail until 08:00. The function must be idempotent and its errors raise an alarm.
