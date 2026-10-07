# ADR 0004: Alternating-user credential rotation

Status: Accepted (2026-10-05)

## Context

Services must not use the master user, and credentials must rotate without a visible failure.

## Decision

Services and the ingest job use `calc_app`, a login user in the group role `calc_app_rw` (select and insert on `calculations`), created by the migration without a password. The secret `sm-useast2-oecalc-app-<env>` rotates every 3 days with the alternating-user strategy (two users, the previous one stays valid for one cycle) through a Lambda in the private subnets, within 09:00 to 17:00 Panama time. The master user (secret managed by RDS, also rotated every 3 days) is read only by the migrate task, the rotation function and administrators. A failed rotation sends an SNS email.

## Consequences

Connections are pooled and reconnect with the current secret version on rejection. The rotation function needs the VPC and Secrets Manager egress, which is why the window is inside the NAT hours.
