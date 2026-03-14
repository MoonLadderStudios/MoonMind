# Contract: Canonical Workflow Naming Surfaces

## Purpose

Define canonical naming requirements for runtime and documentation surfaces during legacy workflow naming migration.

## Canonical Surface Contract

| Surface | Legacy Pattern | Canonical Pattern | Contract Rule |
|---|---|---|---|
| Environment keys | `WORKFLOW_*`, `AUTOMATION_*` | `WORKFLOW_*` | Runtime and docs must expose canonical keys only for active configuration guidance. |
| Settings namespace | `workflow` | `workflow` | Config references, docs, and contracts use `workflow` naming. |
| API routes | `/api/spec-automation/*`, `/api/workflows/agentkit/*` | `/api/workflows/*` | Canonical route family is `/api/workflows/*`; legacy routes are not silently aliased. |
| Schema/type names | `Workflow*` | `Workflow*` | Contracts and examples use `Workflow*` identifiers. |
| Metrics namespace | `moonmind.workflow*`, `automation.*` | `moonmind.workflow*` | Observability docs and runtime emitters use canonical namespace names. |
| Artifact roots | `var/artifacts/workflows` | `var/artifacts/workflow_runs` or `var/artifacts/workflows` | Artifact path naming must be canonical and consistent per document/component context. |

## Behavioral Invariants

- Canonical naming updates must not change queue semantics.
- Canonical naming updates must not modify billing-relevant values.
- `codex.model` and `codex.effort` pass-through behavior remains exact.
- Unsupported legacy runtime inputs fail deterministically with actionable errors.

## Validation Contract

1. Docs/spec validation scan returns no unapproved legacy matches.
2. Runtime scan returns no unapproved legacy runtime naming matches.
3. Unit tests pass using `./tools/test_unit.sh`.
4. Requirements traceability covers all `DOC-REQ-001` through `DOC-REQ-011`.
