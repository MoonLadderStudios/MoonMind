# GitHub Event Trigger Delivery (Opt-In, First Slice)

**Document Class:** Canonical declarative
**Viewpoint:** Module Contract Specification
**Status:** Implemented (first slice: `issues/labeled`)
**Owners:** MoonMind Engineering
**Updated:** 2026-09-22
**Audience:** Operators and contributors wiring repository events to presets
**Authority:** Opt-in event admission and durable delivery receipt for one repository event. App enrollment stays with the repository-connections surface, preset semantics stay with the preset catalog, and the `@mm` command language stays with its owning vocabulary.
**Owning Surface:** `POST /api/v1/github/events` ingress, `github_event_delivery_receipts` receipt table, existing Temporal execution admission
**Related Docs:** [Skill PR Resolver](SkillGithubPrResolver.md), [Docker Compose Update](../Steps/DockerComposeUpdateSystem.md)
**Related Implementation:** `moonmind/workflows/adapters/github_event_delivery.py`, `api_service/api/routers/github_event_webhook.py`, `api_service/services/github_event_dispatch.py`, `api_service/db/models.py::GitHubEventDeliveryReceipt`, `tests/unit/workflows/adapters/test_github_event_delivery.py`, `tests/unit/api/routers/test_github_event_webhook_3967.py`

## 1. Purpose

One explicitly enabled repository event starts an authorized existing preset and exposes the resulting workflow. This thin integration adds no executor, scheduler, account system, webhook microservice, or general event platform. First slice: an explicitly authorized `issues/labeled` event only. Every other event/action is ignored safely.

## 2. Setup

Default local-only operation is unchanged. Public HTTPS ingress is explicit operator setup (reverse proxy to the existing API process); this router opens no new port.

1. Store the GitHub webhook secret in the existing Managed Secrets owner under a slug (default `github-webhook-secret`). Override the slug with `MOONMIND_GITHUB_WEBHOOK_SECRET_SLUG`. The secret value never leaves the Secrets owner; only the slug is configured.
2. Configure the App webhook to deliver `issues` events with `X-Hub-Signature-256` to `POST /api/v1/github/events`.
3. Opt in with `MOONMIND_GITHUB_EVENT_TRIGGERS`, a JSON list of trigger bindings. Each entry requires `repository` (`owner/repo`), `installation_id`, `permitted_actors` (non-empty), `label`, and `preset_slug`, and accepts `event_name` (default `issues`), `action` (default `labeled`), `execution_limits`, `publication_intent` (default `none`), `enabled` (default `true`), `webhook_secret_slug`, `max_age_seconds` (default `600`), and `allow_fork_content` (default `false`). Empty/missing means nothing is opted in.
4. Run database migrations so `github_event_delivery_receipts` exists.

Example:

```json
[
  {
    "name": "label-triage",
    "repository": "acme/repo",
    "installation_id": "12345",
    "event_name": "issues",
    "action": "labeled",
    "permitted_actors": ["alice"],
    "label": "mm-ready",
    "preset_slug": "triage-preset",
    "execution_limits": {"maxModelBudgetUsd": 5},
    "publication_intent": "none",
    "enabled": true
  }
]
```

## 3. Runtime contract

* The exact bounded raw body (max 1 MiB) is verified with `X-Hub-Signature-256` (constant-time) before any field is trusted. Oversized, unsigned, or mis-signed deliveries are refused with no launch and no receipt.
* The durable receipt (`delivery_key = github-delivery:v1:{installation}:{repo}:{delivery}`) is inserted before acknowledgment or dispatch and records the scoped delivery id, payload digest, decision, preset, and execution reference. Full secret-bearing payloads are never written to logs or history.
* Redelivery of the same body reuses the stored logical request and execution reference. A changed body under the same delivery id is a `409` conflict, never fresh work. A receipt with no dispatch evidence (`admitted_pending`) re-attempts under the same stable identity key, which is also the Temporal idempotency key.
* Current trigger authority is re-resolved immediately before dispatch; a trigger disabled after receipt is rejected (`revoked_before_dispatch`) with no launch.
* Stale events (older than the trigger's `max_age_seconds`) and receipts older than the 7-day retention window get an explicit safe disposition, never a launch. Self-generated App bot events and unsupported events are ignored safely.
* Dispatch is instance-owned through the existing `TemporalExecutionService` admission boundary (pause guard, Skill input validation, provider profile runtime, plan source). Dispatch failures stay `admitted_pending` with a safe reason code so a later redelivery can reconcile the lost start.

## 4. Limitations

* One configured receiving deployment per trigger. Independent deployments do not share the dedupe table; fleet-wide exactly-once behavior is not claimed and no inter-deployment coordination was added.
* Receipt insertion is not proof of exactly-once downstream effects; uncertain external mutations keep their existing reconciliation owners.
* The configured preset must be dispatchable through ordinary Temporal admission; otherwise deliveries stay pending with a recorded safe reason and no spend.
* GitHub does not automatically redeliver failed webhook deliveries; bounded recovery is manual redelivery (same delivery id reuses the request) or a same-body retry. There is no polling service, unlimited catch-up, or silent replay of old spending intent.
* Diagnostics reuse existing surfaces: receipt rows (queryable by delivery key/repository) carry the workflow reference, and safe reason codes are returned to the caller and logged. No dedicated delivery dashboard was added.
* Live installation/ingress verification is separately authorized and reported honestly; hermetic CI (signed fixtures over the real ingress, receipt, and dispatch path) is the code-publication boundary.

## 5. Verification

* `tests/unit/workflows/adapters/test_github_event_delivery.py` — trigger resolution, signature matrix, validation gates, redelivery/conflict classification, retention.
* `tests/unit/api/routers/test_github_event_webhook_3967.py` — signed opted-in fixture traverses ingress, durable receipt, admission, and dispatch; negative matrix (bad/missing signature, unauthorized actor, wrong repository, disabled trigger, fork content, self bot, unsupported event) causes no launch; duplicate reuses the execution; changed body conflicts; receipt survives engine restart.
* Broader regression plus real Temporal/database boundaries run in GitHub Actions CI.
