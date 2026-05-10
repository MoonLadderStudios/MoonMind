# Data Model: Agent Tool-Surface Isolation

Source traceability: `MM-680` - Generalizable Agent Tool-Surface Isolation for MoonMind-Mediated Workflows.

## Managed Agent Session

Represents one MoonMind-launched managed runtime session.

Fields:
- `session_id`: stable runtime/session identifier.
- `workflow_id` / `run_id`: Temporal execution identity for attribution.
- `runtime`: managed runtime adapter identifier such as Codex, Claude Code, Gemini, or future adapter.
- `service_identity_ref`: non-secret reference to the MoonMind-managed service identity used for the run.
- `operator_identity_present`: boolean audit field that must be false for managed agent runtime sessions.
- `resolved_skillset_ref`: artifact or snapshot reference for the resolved active skill set.
- `selected_skill`: optional selected skill name.
- `surface_contract_ref`: compact reference to the resolved runtime surface contract.
- `workspace_root`: workspace path reference used only for diagnostics and local execution boundaries.

Validation rules:
- Managed sessions must reject operator-account OAuth or account-level connector grants before runtime launch.
- `selected_skill` must resolve inside `resolved_skillset_ref` when present.
- `surface_contract_ref` must be available before any external-service-capable runtime starts.

State transitions:
- `requested` -> `validated` when service identity and surface contract pass.
- `validated` -> `running` when runtime starts with enforced surfaces.
- `validated` -> `rejected` when identity or surface validation fails.
- `running` -> `completed` / `failed` with diagnostic refs.

## Skill Surface Contract

Closed declaration of surfaces allowed for one managed session.

Fields:
- `skill_name`: selected skill or skill-set entry.
- `snapshot_id`: resolved immutable skill snapshot.
- `allowed_tools`: exact MoonMind tool identifiers visible to the session.
- `allowed_mcp_servers`: exact MCP server identifiers visible to the session.
- `allowed_connectors`: exact MoonMind-mediated connector surfaces visible to the session.
- `allowed_egress`: normalized destination rules allowed through MoonMind egress mediation.
- `publish_authority`: enum: `none_in_agent`, `moonmind_activity_only`, or explicit future value.
- `diagnostic_policy`: compact redaction and event-publication policy.

Validation rules:
- Empty or missing required surface sets fail closed unless the skill explicitly declares no access for that surface class.
- Requested runtime tool/MCP/connector/egress sets must be a subset of the contract.
- `publish_authority` for managed agent sessions must not grant in-agent branch push or pull request creation authority for this story.

## Runtime Surface Decision

Per-launch validation outcome.

Fields:
- `decision`: `allowed` or `denied`.
- `surface_type`: `tool`, `mcp`, `connector`, `egress`, `publish`, or `identity`.
- `surface_id`: sanitized identifier for the requested surface.
- `contract_ref`: source contract reference used for the decision.
- `reason`: stable reason code such as `not_declared`, `operator_identity_forbidden`, `publish_not_owned_by_agent`, or `egress_not_allowed`.
- `diagnostic_ref`: optional artifact or workflow metadata ref.

Validation rules:
- Denied decisions must not expose raw tokens, cookies, authorization headers, full environment values, or tokenized URLs.
- Denied launch-critical surfaces stop runtime startup.
- Denied in-session attempts are recorded and fail without external mutation.

## Publish Operation

MoonMind-owned branch and pull request side-effect boundary.

Fields:
- `repo`: repository slug.
- `head`: head branch.
- `base`: base branch.
- `last_recorded_remote_sha`: optional remote SHA used for lease-aware push.
- `push_status`: `pushed`, `no_commits`, `protected_branch`, `lease_conflict`, `failed`, or similar stable value.
- `pull_request_url`: adopted or created pull request URL.
- `created`: boolean indicating whether a new PR was created.
- `adopted`: boolean indicating whether an existing PR was adopted.
- `head_sha`: resulting head SHA when known.
- `retryable`: boolean for conflict/failure classification.
- `diagnostic_ref`: optional sanitized diagnostic ref.

Validation rules:
- Existing head/base pull requests are success states and must return `adopted=true`, `created=false`, and a URL.
- Lease misses must be classified as retryable conflicts and include enough sanitized state for recovery.
- Secret material must never be included in payloads, logs, or artifacts.

## Isolation Diagnostic

Sanitized operator-visible evidence for denied surfaces and publish reconciliation.

Fields:
- `kind`: stable event kind such as `identity_rejected`, `surface_rejected`, `egress_blocked`, `direct_publish_denied`, `pull_request_adopted`, or `publish_lease_conflict`.
- `workflow_id` / `run_id`: Temporal context.
- `session_id`: optional managed session context.
- `surface_type`: optional denied or reconciled surface class.
- `surface_id`: sanitized identifier.
- `reason`: stable reason code.
- `contract_ref`: optional compact contract reference.
- `artifact_ref`: optional durable evidence reference.
- `timestamp`: activity/runtime timestamp when emitted outside deterministic workflow code.

Validation rules:
- Diagnostics must be compact enough for workflow metadata or artifact refs.
- Diagnostics must redact tokens, credentials, raw authorization headers, cookies, and private key material.
- Workflow code should carry refs and compact metadata only; rich telemetry should be produced at activity/runtime boundaries.
