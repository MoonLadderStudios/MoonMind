# MoonSpec Document Model

This document defines the document classes MoonSpec workflows operate on, the precedence rules between them, and the reconciliation expectation that keeps canonical documentation aligned with verified implementation. MoonSpec skills and presets reference this document instead of restating its rules.

## Document Classes

### Canonical declarative documents

- **Location**: long-lived files under `docs/`.
- **Content**: desired state — architecture, contracts, operator-visible behavior, and target semantics.
- **Lifecycle**: version-controlled and durable. These are the primary source of truth for what the system is and how it should behave.
- **Constraints**: canonical documents must stay declarative. Migration narratives, phased rollout sequencing, status checklists, and implementation backlogs must not become their primary framing.

### Temporary execution artifacts

- **Location**: `specs/`, `artifacts/`, and `artifacts/story-breakdowns/` (all gitignored).
- **Content**: derived, run-scoped working material — `spec.md`, `plan.md`, `tasks.md`, `research.md`, `contracts/`, story breakdown JSON/markdown, discovery ledgers, and tool handoffs.
- **Lifecycle**: disposable. They exist to execute one run of work and are never cited as authority for desired state. A spec is a temporary derived view of its canonical source, not a replacement for it.

### Imperative working documents

- **Location**: `docs/tmp/` or gitignored handoff paths.
- **Content**: checklists, status trackers, migration and rollout plans, cleanup sequences — anything whose primary framing is steps, phases, checkboxes, or status.
- **Lifecycle**: time-bound. Delete or archive them when the work completes.

## Classification And Fallback Rule

A document is **declarative** when it describes what the system is or should be. A document is **imperative** when its primary framing is steps, phases, checkboxes, or status. Mixed documents are classified by their primary framing.

MoonSpec breakdown workflows prefer declarative input and must select a declarative source when one is provided or can be resolved. When no declarative design is available, breakdown workflows may use imperative input as a resilience fallback: extract the underlying desired system behavior, constraints, and operator outcomes from the steps instead of requiring explicit user confirmation. The output must record that source as imperative input and must not treat checklist steps, phases, or status rows as story boundaries unless they are independently valuable and testable user or operational outcomes.

MoonSpec specification workflows still produce declarative, single-story specs. If a spec starts from imperative input, it must translate that input into desired-state requirements and preserve assumptions or unresolved choices explicitly.

## Precedence Rule

When a derived artifact (`spec.md`, `stories.json`, `plan.md`, `tasks.md`) conflicts with its canonical source document:

1. The canonical document wins by default.
2. If implementation or verification evidence shows the canonical document itself is impossible to satisfy as written, unclear, or internally inconsistent, the canonical document must be updated through doc reconciliation — or the conflict escalated — rather than silently overridden in the derived artifact.
3. Derived artifacts never resolve such conflicts on their own authority.

## Reconciliation Expectation

Implementation runs that discover canonical-document drift end with a doc-reconciliation pass (see the `moonspec-doc-reconcile` skill): when a non-trivial change lands, its durable decisions are reflected in the owning `docs/` files.

Reconciliation updates a canonical document only when discoveries **definitely require** it — the document is impossible, unclear, or inconsistent:

- **Impossible**: verified evidence shows a documented claim cannot be satisfied as written or was factually incorrect independent of the implementation.
- **Unclear**: the implementation had to resolve a documented ambiguity, and the document must record the verified resolution to stay usable.
- **Inconsistent**: the implementation correctly resolved an internal contradiction in the document, and the document must record the resolution to stay coherent.

Divergence alone never qualifies: an implementation that differs from a clear, consistent, satisfiable document is an implementation defect or an unapproved desired-state change, and deliberate verification-backed divergence is escalated for an owner decision instead of applied. Stylistic preferences, additions for completeness, speculative improvements, and unverified observations never qualify either. Reconciliation preserves desired-state framing: it never downgrades a canonical document to match buggy or incomplete code, and it never inserts imperative content into canonical files. Updates that would conflict with repo guidance, README, or architecture direction are escalated as tracked issues instead of applied.

## Documentation Architecture Standard

The [Documentation Architecture Standard](../DocumentationArchitecture.md) defines the documentation viewpoints (architecture, design, and contract viewpoints) that organize canonical declarative documents. Its viewpoint rules **refine** canonical declarative documents; they do **not** replace this model's lifecycle, classification, fallback, precedence, or reconciliation rules. The document classes, classification and fallback rule, precedence rule, and reconciliation expectation defined above remain authoritative, and the standard is read as a refinement layered on top of them rather than a substitute. See the standard for the rules themselves; they are not restated here.

### Read order

Read MoonSpec documentation in this order:

1. **MoonSpec Document Model** (this document) — document classes, classification and fallback, precedence, and reconciliation.
2. **[Documentation Architecture Standard](../DocumentationArchitecture.md)** — viewpoint rules that refine the canonical declarative documents.
3. **Project-local architecture / design / contract docs** — the canonical declarative documents for the project at hand.
4. **Optional project-local implementation scratch** — temporary execution artifacts and imperative working documents, when present.

Downstream projects may adapt this read order with minor local additions, such as repo-level AGENTS.md guidance or engineering and owner matrices, without changing the relative ordering above.
