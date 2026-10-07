# ADR 0002: Token validation at the load balancer

Status: Accepted (2026-10-05)

## Context

The web page sends the Cognito ID token to the API. The owner wanted validation before traffic reaches the services.

## Decision

The ALB listener rules use the `jwt-validation` action (issuer, JWKS of the pool, `aud`, `token_use`) on every API path; a preflight rule by method comes first without a token, and the default action is a 404. Services never authorize; they only decode `sub` for logging. The services accept traffic only from the load balancer security group.

## Consequences

Rejections are produced by the load balancer, so their status and body are not customizable. The load balancer must reach the JWKS URL: its security group needs egress TCP 443 (rule `alb_jwks`); without it the rule fails with a 500. The smoke test sends no valid token, so an alarm on `HTTPCode_ELB_5XX_Count` (sum of at least 5 in 5 minutes) covers that failure. The description of the load balancer security group is not edited because that forces its replacement.
