# Documentation Architecture Standard

**Status:** Canonical standard for MoonMind documentation authority and rationale placement
**Updated:** 2026-06-24
**Audience:** Contributors, operators, runtime authors, integration authors, and anyone resolving conflicting documentation
**Purpose:** Define the authority rules that govern MoonMind's canonical documentation — specifically, which canonical document controls a claim when two canonical documents disagree, and where significant design rationale must live.

This standard is the **Documentation Architecture Standard** referenced by the precedence ladder below. It sits beneath the Constitution and the MoonSpec Document Model and above the individual architecture, concept, contract, and design views. It complements, and does not restate, the cross-class precedence rule (canonical declarative documents over derived/imperative artifacts) defined in `.specify/memory/constitution.md` (principles **XIV**, **XV**) and `docs/Workflows/MoonSpecDocumentModel.md`. Those documents resolve conflicts *between document classes*; this standard resolves conflicts *between canonical documents of the same class*.

> **Traceability.** This standard is authored under source epic **MM-900** ("Implement MoonSpec Documentation Architecture Standard") and story **MM-903** ("Define authority rules in the standard: canonical-vs-canonical precedence ladder and embedded design-rationale policy"), covering design requirements **DESIGN-REQ-008** (canonical-vs-canonical precedence) and **DESIGN-REQ-009** (embedded design-rationale policy). MM-903 was sequenced early because the authority layer is the highest-risk part of the standard.

---

## Canonical-vs-Canonical Precedence

Two canonical declarative documents can disagree about the same claim — for example, a system view and a module view describing dependency direction differently, or a design view and a contract specification describing a DTO shape differently. When they do, the conflict is resolved **by authority scope, not by file age, edit recency, word count, or which document is more convenient to change.** The document whose scope *owns* the kind of claim in question is authoritative; the other document is the one that must be corrected.

### Precedence ladder

Canonical declarative documents rank by the breadth of authority their scope carries. Higher levels govern broader, more foundational desired state; lower levels specialize within the bounds the higher levels set. When two documents make conflicting claims, the document at the higher level wins **for claims within its scope**, and the lower-level document is reconciled to match.

1. **Constitution / Document Model** — `.specify/memory/constitution.md` and `docs/Workflows/MoonSpecDocumentModel.md`. Non-negotiable principles, document classification, and the cross-class precedence rule. Governs everything below.
2. **Documentation Architecture Standard** — this document. Governs how canonical documents relate to one another: same-class precedence, conflict handling, and where rationale lives.
3. **System Architecture View** — the top-level system architecture (e.g. `docs/MoonMindArchitecture.md`). Owns system-wide structure, the orchestration model, and cross-module boundaries and dependency direction.
4. **Cross-Cutting Concept View** — concepts that span modules (security, observability, artifact model, skill runtime, Temporal determinism). Owns the system-wide semantics of a concern wherever it appears.
5. **Module Architecture View** — the internal architecture of a single module or subsystem. Owns that module's internal structure within the boundaries the system view sets.
6. **Module Contract Specification** — the interface a module exposes (DTO shapes, payload schemas, activity/signal/update signatures, API surfaces). Owns the exact shape and semantics of what crosses a module boundary.
7. **System/Feature Design View** — how a feature is realized using the modules and contracts above. Owns feature-level composition and behavior, bounded by the contracts it consumes.
8. **Migration / Implementation / Rollout / Status documents** — sequencing, phased plans, cutover notes, and status (canonically confined to `docs/tmp/` and gitignored handoffs per Constitution **XV**). These describe *how and when* desired state is reached. **They never govern desired state itself** and never win a conflict against any level above them; a migration document that contradicts the target architecture is wrong about the target by definition.

A higher-level document is only authoritative for claims **within its scope**. A system view does not override a contract specification on the exact byte layout of a DTO, because DTO shape is the contract specification's scope (level 6); the system view governs whether that module is *allowed to depend on* another module (level 3). Authority is by ownership of the claim type, applied along this ladder — not blanket dominance of higher levels over all content of lower ones.

### Conflict-handling procedure

When two canonical documents disagree, resolve it deterministically:

1. **Identify the claim type.** What is actually in conflict — a dependency direction, a DTO/payload shape, a cross-cutting semantic, a feature behavior, a piece of rationale, or a sequencing/status statement?
2. **Identify the owning authority scope.** Map the claim type to the level on the ladder whose scope owns it (e.g. cross-module dependency direction → System Architecture View; DTO shape → Module Contract Specification).
3. **Treat the owner as authoritative.** The owning document's statement is the desired state. The non-owning document is the one that is wrong and must be changed — regardless of which file was edited more recently or is easier to touch.
4. **Reconcile in the same PR when feasible.** Update the non-owning document to agree with the owner in the same change that surfaced the conflict, so canonical documentation never knowingly ships self-contradictory. This operationalizes the reconciliation expectation in the Document Model.
5. **Escalate when the conflict is foundational.** If the conflict implicates the Constitution, the Document Model, this standard, or a published contract — or if reconciling in place would change non-negotiable principles or break a contract other documents depend on — do not resolve it unilaterally. Open a Jira issue and escalate, as required for constitution/architecture-direction conflicts by Constitution **XIV** and the Document Model reconciliation rules.

### Worked examples

These conflicts are resolved purely by authority scope:

- **Module view vs system view — dependency direction.** A module architecture view states the module calls into another module; the system architecture view forbids that dependency direction. *Resolution:* cross-module dependency direction is owned by the **System Architecture View** (level 3). The system view wins; the module view is corrected to respect the allowed boundary. File age and which document was written first are irrelevant.
- **Design view vs contract spec — DTO shape.** A feature design view shows a payload with one field set; the module contract specification defines a different field set for the same DTO. *Resolution:* DTO/payload shape is owned by the **Module Contract Specification** (level 6). The contract wins; the design view is reconciled to consume the contract's actual shape. A design view never redefines a contract it depends on.
- **Rationale vs normative rule.** A "why" explanation embedded in one document is read as if it loosened a normative rule stated elsewhere (e.g. rationale says "for performance we sometimes inline" read as permission to violate a layering rule). *Resolution:* rationale explains and justifies; it does not grant exceptions. The **normative rule** in the owning document controls; the rationale is clarified so it cannot be misread as overriding the rule. Rationale never outranks the requirement it accompanies.
- **Migration "support both paths" vs target architecture.** A migration/rollout document says the system "supports both the old and new path"; the target architecture describes only the new path as desired state. *Resolution:* migration/status documents are **level 8** and never govern desired state. The target architecture wins. "Support both paths" is at most a time-bound transitional note belonging in `docs/tmp/` (Constitution **XV**); it does not make dual-path support part of the canonical desired state, and per **Pre-Release Velocity: Delete, Don't Deprecate** (Constitution **XVI**) it is removed once the migration completes.

---

## Embedded Design-Rationale Policy

Rationale — *why* the desired state is shaped the way it is — is part of the canonical record, not a separate artifact. A reader resolving a conflict or evaluating a change must be able to understand both the rule and its justification **from the same canonical document that owns the rule**, without consulting any separate log.

Significant design rationale **MUST be embedded in the owning architecture, design, or contract document** — the same document that, on the precedence ladder, owns the claim the rationale justifies. Rationale for a system boundary lives in the System Architecture View; rationale for a DTO's shape lives in its Module Contract Specification; rationale for a feature's composition lives in its System/Feature Design View. Rationale follows its rule.

Two placement forms are recommended:

- **`## Design Rationale`** — a top-level section in a document, collecting the document-wide "why" behind its most significant or cross-cutting decisions. Use this when a document has several material decisions that benefit from being explained together (the Constitution's per-principle `Rationale:` lines and `docs/Workflows/MoonSpecDocumentModel.md`'s "Reconciliation Expectation" framing are existing examples of embedded, document-local rationale).
- **`### Rationale`** — a localized subsection placed immediately after the specific rule, contract clause, or structural decision it explains. Use this when the "why" is tightly scoped to one rule and is most useful read alongside it.

Both forms keep rationale inside the canonical document so it cannot drift from the rule it justifies and so it is reconciled in the same PR when the rule changes. Rationale is explanatory, not normative: it justifies a rule but never loosens, overrides, or creates an exception to it (see the rationale-vs-normative-rule example above).

### No separate decision logs, ADRs, or `decisions/` directories

MoonMind **does not** maintain Architecture Decision Records (ADRs), separate decision logs, or a `decisions/` directory, and this standard imposes no requirement or recommendation to create any. There is no obligation — and contributors **MUST NOT** introduce one — to record design decisions in a parallel artifact outside the owning canonical document.

A reader **MUST NOT** be required to consult a separate document to understand the current desired state or the rationale behind it: the owning architecture, design, or contract document is the single place where both the decision and its justification live. A standalone decision log would be a second source of truth that drifts from the canonical document, contradicting Docs-First Development (Constitution **XIV**), the desired-state framing of canonical docs (Constitution **XV**), and Pre-Release Velocity (Constitution **XVI**). Time-bound decisions made during a migration belong with the migration material under `docs/tmp/` and are deleted when the work completes; they are never promoted into a durable decision-log surface.
