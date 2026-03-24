# Microservices Trajectory (SaaS-Ready)

## Current State
- Monolith API with scheduler and notification dispatch.
- Outbox pattern introduced via `outbox_events`.
- Worker entrypoint: `scripts/run_outbox_worker.py`.

## Extraction Order
1. Notifications service
2. Admissions service
3. Rendez-vous service
4. IA tools service

## Event Contracts
- `notification.preferred.v1`
  - Schema: `app/events/notification.preferred.v1.json`
  - Producer: scheduler/API
  - Consumer: outbox worker

## Mandatory Guardrails
- Versioned event schemas.
- Idempotency key on producer and consumer side.
- Tenant-aware routing (`tenant_id` required).
- Retry/backoff with dead-letter handling.

## Observability
- Structured logs include `tenant_id`, `event_id`, `event_type`, `attempts`.
- Metrics to expose:
  - outbox pending count
  - outbox failure rate
  - send latency p95
