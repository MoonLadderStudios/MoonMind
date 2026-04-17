# Data Model: Proposal Promotion Preset Provenance

## Authored Preset Metadata

Represents optional task-level provenance copied from an authored task snapshot.

- Fields: preset binding identity, authored preset references, include tree summary, detachment state when available.
- Rules: metadata is advisory UX/reconstruction context and never executable runtime logic.
- Validation: promotion preserves it by default when present and validates the merged flat task payload as usual.

## Step Source Provenance

Represents optional per-step source metadata.

- Fields: source kind, binding id, include path, blueprint step slug, detached flag when available.
- Rules: may distinguish manual, preset-derived with preserved binding metadata, and preset-derived flattened-only states.
- Validation: malformed provenance must not be silently converted into executable behavior.

## Flat Task Payload

Represents the execution-ready proposal payload promoted through the normal task creation path.

- Fields: repository, task instructions, runtime, publish policy, flat steps, optional authored preset metadata, optional per-step source provenance.
- Rules: default promotion submits this reviewed payload without live preset catalog lookup or live re-expansion.

## Refresh-Latest Workflow

Represents a future explicit workflow that would intentionally refresh preset contents before submission.

- Fields: explicit operator intent, selected preset bindings, refreshed result, validation evidence.
- Rules: out of scope for default promotion; must never be implied as automatic behavior.
