# ADR 0006: Route 53 weights owned by the webpage repository

Status: Accepted (2026-10-05)

## Context

Several interchangeable backends (`sls`, `ecs`, later EC2 and EKS) must answer on the same host name, and the web page must not change when traffic moves.

## Decision

The webpage repository owns the API certificate, the weighted records and the list of registered backends. Each backend publishes its target in SSM (`/oecalc/<env>/api-backends/<backend>/dns-name` and `hosted-zone-id`) and serves the neutral prefix `/api/v1` plus its own. This repository only reads the certificate (`/oecalc/<env>/api-certificate-arn`) and publishes its target.

## Consequences

Switching or rolling back is a weight change in another repository and needs a deployment of the webpage infrastructure. A backend appears there only after it has published its target, which currently needs the webpage to be deployed once more. A backend must not be destroyed before the weights are moved away from it.
