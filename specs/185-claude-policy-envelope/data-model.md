# Data Model: Claude Policy Envelope

## ClaudePolicyEnvelope

Represents the effective compiled policy for one canonical Claude managed session.

Fields:
- `policy_envelope_id`: stable non-blank identifier for this envelope version.
- `session_id`: canonical Claude managed-session identifier.
- `provider_mode`: one of `anthropic_api`, `bedrock`, `vertex`, `foundry`, or `custom_gateway`.
- `managed_source_kind`: one of `none`, `server_managed`, or `endpoint_managed`.
- `policy_fetch_state`: one of `not_applicable`, `cache_hit`, `fetched`, `fetch_failed`, or `fail_closed`.
- `managed_source_version`: optional non-blank source version or hash.
- `policy_trust_level`: one of `endpoint_enforced`, `server_managed_best_effort`, or `unmanaged`.
- `permissions`: compiled permission controls.
- `sandbox`: compiled sandbox controls.
- `hooks`: compiled hook controls and registry evidence.
- `mcp`: compiled MCP server policy.
- `memory`: compiled memory policy.
- `bootstrap_templates`: tuple of BootstrapPreferences represented as templates only.
- `security_dialog_required`: whether startup requires an interactive security dialog.
- `version`: positive integer envelope version.
- `admin_visibility`: detailed policy visibility for administrator/operator surfaces.
- `user_visibility`: coarse policy status for non-admin surfaces.

Validation rules:
- `version` must be at least 1.
- `fail_closed` envelopes must not expose permissive effective lower-scope settings.
- Bootstrap templates must have `kind = bootstrap_template`.
- Detailed fetch evidence is admin-visible; non-admin visibility is coarse unless authorized elsewhere.

## ClaudePolicySource

Represents one candidate managed policy source.

Fields:
- `source_kind`: `server_managed`, `endpoint_managed`, `local_project`, `shared_project`, `user`, or `cli`.
- `supported`: whether this source can participate in managed-source resolution.
- `settings`: compact policy settings payload.
- `version`: optional source version.
- `fetch_state`: source fetch state.
- `risky_controls`: flags such as managed hooks or managed environment variables.

Validation rules:
- Managed source precedence considers only server-managed and endpoint-managed sources.
- Lower scopes are observability-only and cannot override managed settings.

## ClaudePolicyHandshake

Represents startup readiness after policy resolution.

Fields:
- `session_id`: canonical Claude managed-session identifier.
- `policy_envelope_id`: optional envelope identifier.
- `state`: `ready`, `security_dialog_required`, `security_dialog_accepted`, `security_dialog_rejected`, `fail_closed`, or `blocked`.
- `reason`: optional human-readable reason.
- `interactive`: whether the session can show an interactive dialog.

Validation rules:
- `fail_closed` state may omit `policy_envelope_id` when no permissive envelope is produced.
- Non-interactive sessions requiring a dialog must be blocked or fail closed rather than accepted.

## ClaudePolicyEvent

Append-only policy lifecycle event.

Fields:
- `event_id`: stable non-blank identifier.
- `session_id`: canonical Claude managed-session identifier.
- `policy_envelope_id`: optional envelope identifier.
- `event_type`: `policy.fetch.started`, `policy.fetch.succeeded`, `policy.fetch.failed`, `policy.dialog.required`, `policy.dialog.accepted`, `policy.dialog.rejected`, `policy.compiled`, or `policy.version.changed`.
- `occurred_at`: timezone-aware timestamp.
- `metadata`: compact event metadata.

Validation rules:
- Event metadata must remain compact enough for workflow/activity payloads.
- Dialog events must only appear when a dialog state is relevant.

## State Transitions

Policy handshake:

```text
ready
security_dialog_required -> security_dialog_accepted -> ready
security_dialog_required -> security_dialog_rejected -> blocked
fail_closed
blocked
```

Policy fetch:

```text
not_applicable
cache_hit
fetched
fetch_failed
fail_closed
```

## Relationships

- One Claude managed session can have many policy envelope versions over time.
- One policy envelope belongs to exactly one Claude managed session.
- One policy handshake references one session and optionally one envelope.
- Policy events reference one session and optionally one envelope.
