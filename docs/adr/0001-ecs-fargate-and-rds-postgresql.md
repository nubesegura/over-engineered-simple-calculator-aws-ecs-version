# ADR 0001: ECS Fargate and RDS PostgreSQL

Status: Accepted (2026-10-05)

## Context

The project exists to try a technology other than the serverless sibling, with the same business rules and API contract. It is a test: raised, reviewed and destroyed.

## Decision

Run five HTTP services and the ingestion job on ECS Fargate (ARM64, one task each, no auto scaling) and store data in RDS for PostgreSQL (`db.t4g.micro`, Single-AZ, gp3 20 GB) with direct connections from the services.

## Consequences

Fixed hourly costs (database, load balancer, NAT, tasks) instead of pay-per-request. Accepted exceptions to the team guidelines: Single-AZ, no AWS Backup plan, no RDS Proxy. Schema changes need a migration task in the pipeline. The history is synchronous, with no queue. Services must handle credential rotation by reconnecting.
