# ADR 0005: Self-signed certificates between load balancer and tasks

Status: Accepted (2026-10-05)

## Context

Traffic inside the VPC should still be encrypted, but a certificate authority for the tasks is excessive for a test project.

## Decision

Each task generates an in-memory ECDSA P-256 self-signed certificate valid one day at start, and the target groups use HTTPS. The load balancer does not validate target certificates.

## Consequences

Encryption in transit without certificate management. The certificate does not authenticate the hop; only the load balancer security group can reach the tasks. The private key stays in the task and is never logged.
