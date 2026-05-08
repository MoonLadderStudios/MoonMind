# Data Model: Refresh Managed Runtimes After Derived Skill Activation

## Derived Skill Snapshot

Represents the immutable active Skill set approved after an on-demand request.

Fields:
- `snapshot_id`: stable identifier for the derived snapshot.
- `parent_snapshot_ref`: previous active snapshot identifier.
- `resolved_skillset_ref`: compact manifest/artifact reference for the derived set.
- `skills`: compact Skill metadata; must not include Skill body content.
- `source_trace`: lineage metadata including requested Skills and request origin.

Validation rules:
- Must reference a parent snapshot before activation.
- Must not be announced active until materialization verification succeeds.
- Must not embed full Skill bodies in workflow or activity payloads.

## Runtime Projection

Represents the managed runtime-visible active Skill path.

Fields:
- `visible_path`: runtime-readable active Skill path.
- `backing_path`: MoonMind-owned backing directory for the materialized snapshot.
- `canonical_alias_path`: compatibility alias such as `.agents/skills` when safely available.
- `projection_diagnostics`: compact status for created, reused, skipped, blocked, or failed aliases.

Validation rules:
- Must not point at partially written Skill content.
- Must not replace repo-authored Skill sources.
- Must not use local-only overlays as mutable runtime projection state.
- Must preserve the previous active projection when refresh fails.

## Activation Update

Represents the compact runtime-facing activation result.

Fields:
- `status`: `activated`, `denied`, or `no_change`.
- `activation_summary`: short text for the managed runtime.
- `activation_timing`: immediate/atomic activation or next-turn/controlled steer-point activation guidance.
- `materialization`: compact mode, manifest, visible path, and verification status.
- `diagnostics`: safe failure details when activation is denied.

Validation rules:
- Must be emitted only after the derived snapshot is ready or after a safe failure is known.
- Must not include Skill bodies, secrets, hidden catalog content, or arbitrary body-readable refs.
- Must distinguish `materialization_failed` from `runtime_refresh_failed`.

## Refresh Failure Diagnostic

Represents safe evidence for why a derived snapshot was not activated.

Fields:
- `code`: structured failure code such as `materialization_failed` or `runtime_refresh_failed`.
- `message`: safe operator-facing summary.
- `active_snapshot_id`: snapshot that remains active.
- `attempted_snapshot_id`: derived snapshot attempted, when safe to expose.
- `projection_diagnostics`: alias/materialization status details.

Validation rules:
- Must preserve the previous active snapshot.
- Must not expose raw secrets, hidden Skill body text, or unrestricted artifact reads.
- Must be compact enough for workflow history.

## State Transitions

```text
derived_snapshot_selected
  -> materializing
  -> verifying
  -> activation_ready
  -> activated

materializing
  -> materialization_failed

verifying
  -> materialization_failed

activation_ready
  -> runtime_refresh_failed
```

Failure transitions keep the previous active snapshot as the runtime-visible state.
