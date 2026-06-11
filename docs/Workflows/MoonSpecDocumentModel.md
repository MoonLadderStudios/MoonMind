# MoonSpec Document Model

This document defines the document classes MoonSpec workflows operate on, the precedence rules between them, and the reconciliation expectation that keeps canonical documentation aligned with verified implementation. MoonSpec skills and presets reference this document instead of restating its rules.

## Document Classes

### Canonical declarative documents

- **Location**: long-lived files under `docs/`, plus `.specify/memory/constitution.md`.
- **Content**: desired state — architecture, contracts, operator-visible behavior, and target semantics.
- **Lifecycle**: version-controlled and durable. These are the primary source of truth for what the system is and how it should behave.
- **Constraints**: canonical documents must stay declarative. Migration narratives, phased rollout sequencing, status checklists, and implementation backlogs must not become their primary framing (Constitution XII).

### Temporary execution artifacts

- **Location**: `specs/`, `artifacts/`, and `artifacts/story-breakdowns/` (all gitignored).
- **Content**: derived, run-scoped working material — `spec.md`, `plan.md`, `tasks.md`, `research.md`, `contracts/`, story breakdown JSON/markdown, discovery ledgers, and tool handoffs.
- **Lifecycle**: disposable. They exist to execute one run of work and are never cited as authority for desired state. A spec is a temporary derived view of its canonical source, not a replacement for it.

### Imperative working documents

- **Location**: `docs/tmp/` or gitignored handoff paths.
- **Content**: checklists, status trackers, migration and rollout plans, cleanup sequences — anything whose primary framing is steps, phases, checkboxes, or status.
- **Lifecycle**: time-bound. Delete or archive them when the work completes (Constitution XII).

## Classification Rule

A document is **declarative** when it describes what the system is or should be. A document is **imperative** when its primary framing is steps, phases, checkboxes, or status. Mixed documents are classified by their primary framing.

MoonSpec breakdown and specification workflows require declarative input. An imperative document is not a valid substitute for a declarative design: decomposing a checklist produces stories that mistake process steps for requirements. When only an imperative document exists, the underlying declarative document must be written or identified first.

## Precedence Rule

When a derived artifact (`spec.md`, `stories.json`, `plan.md`, `tasks.md`) conflicts with its canonical source document:

1. The canonical document wins by default.
2. If implementation or verification evidence shows the canonical document itself is wrong, incomplete, or internally inconsistent, the canonical document must be updated through doc reconciliation — or the conflict escalated — rather than silently overridden in the derived artifact.
3. Derived artifacts never resolve such conflicts on their own authority.

## Reconciliation Expectation

Implementation runs that discover canonical-document drift end with a doc-reconciliation pass (see the `moonspec-doc-reconcile` skill). This operationalizes Constitution XI: when a non-trivial change lands, its durable decisions are reflected in the owning `docs/` files.

Reconciliation updates a canonical document only when discoveries **definitely require** it:

- **Function**: the document describes behavior or contracts that are now factually wrong against the verified implementation.
- **Consistency**: the implementation correctly resolved an internal contradiction or ambiguity in the document, and the document must record the resolution.
- **Best practices**: the implementation deliberately and correctly diverged from a documented approach for a defensible, verification-backed reason.

Stylistic preferences, speculative improvements, and unverified observations never qualify. Reconciliation preserves desired-state framing: it never downgrades a canonical document to match buggy or incomplete code, and it never inserts imperative content into canonical files. Updates that would conflict with the constitution, README, or architecture direction are escalated as Jira issues instead of applied.
