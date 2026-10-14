# MoonSpec Documentation Architecture Standard

**Document Class:** Canonical declarative
**Status:** Current standard and target direction
**Updated:** 2026-06-24
**Audience:** Anyone authoring, classifying, reviewing, or reorganizing durable documentation in a MoonSpec project
**Purpose:** Name the document types a MoonSpec durable docs tree uses, how conflicts between canonical documents of the same class are resolved, and where design rationale lives, so that anyone classifying, writing, or reconciling a doc has one vocabulary, one set of viewpoints, one precedence ladder, and one module-boundary policy.

---

## 1. Scope and Relationship to the MoonSpec Document Model

This document is a **MoonSpec-level strategy**: it describes the desired shape of a durable documentation tree, not a migration plan or a checklist for any one repository.

**It extends, and does not replace, [`docs/Workflows/MoonSpecDocumentModel.md`](Workflows/MoonSpecDocumentModel.md).**

The Document Model defines the three **document classes** (canonical declarative documents, temporary execution artifacts, imperative working documents), the **classification rule** (declarative vs. imperative by primary framing), the **cross-class precedence rule** (canonical wins; derived artifacts never self-resolve conflicts), and the **reconciliation expectation** (canonical docs are realigned with verified implementation). This standard does not restate those rules. It adds a finer taxonomy on top of them:

- the **umbrella vocabulary** for talking about the documentation tree as an architecture description;
- the **canonical declarative viewpoints** that subdivide the canonical class;
- the **imperative working document types** that subdivide the imperative class;
- the **module boundary policy** and the **module-owned contract** document type;
- the **same-class precedence ladder** (§7) and the **embedded design-rationale policy** (§8).

Where the two could appear to disagree, the Document Model governs class, cross-class precedence, and reconciliation; this standard covers only the finer vocabulary, viewpoint taxonomy, same-class precedence, and rationale placement. Terms introduced here refine the base classes rather than substitute for them.

---

## 2. Umbrella Vocabulary

These local MoonSpec terms are deliberately small and stable.

| Term | Definition |
|------|------------|
| **Architecture Description** | The whole documentation tree of a project, treated as one coherent description of the system's architecture: all canonical declarative views plus the module doc sets. There is one per project. |
| **Viewpoint** | A *kind* of canonical declarative document: a reusable template defining a purpose, what such a document owns, its preferred naming, and its concerns. A viewpoint is the schema; a view is an instance. This standard defines five. |
| **View** | A concrete document that conforms to a viewpoint. "System Architecture View" is a viewpoint; `docs/MoonMindArchitecture.md` is a view of it. A view is declarative and desired-state. |
| **Module Doc Set** | The canonical documents owned by one module directory (an architectural boundary or ownership surface): its Module Architecture View, the Module Contract Specifications it owns, and module-scoped design or cross-cutting views. Contracts live inside the owning module doc set, not in a global documentation area. |
| **Canonical Declarative View** | Any view: a long-lived, version-controlled document under `docs/` that describes desired state. Synonymous with the Document Model's "canonical declarative document," scoped to a viewpoint. |
| **Imperative Working Document** | A time-bound document whose primary framing is steps, phases, checkboxes, or status (Migration Plan, Implementation Plan, Rollout Plan, or Status/Checklist Tracker). Identical to the Document Model's imperative class. |

---

## 3. Canonical Declarative Viewpoints

A MoonSpec Architecture Description is composed of views conforming to **five canonical declarative viewpoints**. Every canonical declarative document under `docs/` should be classifiable as one of them, and all five are subject to the Document Model's precedence and reconciliation rules.

### 3.1 System Architecture View

- **Purpose:** Describe the system as a whole: major components, responsibilities, the runtime/orchestration model, and how the parts fit together.
- **Owns:** System-wide structure, cross-component data and control flow, top-level invariants, and architectural direction. Not the internal structure of a module (Module Architecture View) or any single contract (Module Contract Specification).
- **Preferred naming:** `Architecture.md` at the docs root, or `<System>Architecture.md`.
- **Example:** `docs/MoonMindArchitecture.md`.

### 3.2 Module Architecture View

- **Purpose:** Describe the internal architecture of one module: responsibilities, internal structure, and relationships to neighboring modules.
- **Owns:** The module's internal component model, responsibilities, and boundary rationale. Not system-wide structure, and not the formal interface other modules depend on (a Module Contract Specification in the same doc set).
- **Preferred naming:** `<ModuleName>ModuleArchitecture.md` inside the module's doc directory.
- **Example:** `docs/Omnigent/OmnigentModuleArchitecture.md`.

### 3.3 System / Feature Design View

- **Purpose:** Describe a proposed or in-progress design for a capability or feature until it is mature enough to be promoted into an architecture view.
- **Owns:** Design intent, considered options and the chosen approach, and desired feature behavior. Not the imperative build plan, and not settled architecture once the design is realized.
- **Preferred naming:** `<Feature>Design.md`, at the docs root for system-level features or in the owning module doc set.
- **Status field:** A `Status:` field near the top — `Proposed`, `Accepted`, `Implemented`, or `Superseded` — distinguishes design intent from realized architecture.
- **Promotion:** When a design reaches `Implemented`, its settled substance is promoted into the appropriate architecture view or durable `<SystemName>System.md` document, and the design is marked `Superseded` (pointing at its successor) or removed. A design view is transitional, so realized design that exists only there is treated as a gap to fix.
- **Example:** A `docs/Workflows/StepReviewGateSystem.md`-style design carried at `Status: Accepted` while the capability is designed.

### 3.4 Module Contract Specification

- **Purpose:** Define the formal interface a module exposes: API/contract surface, payload shapes, invariants, and compatibility expectations.
- **Owns:** The definition of one module's contract, which consumers cite as the source of truth. Not the module's internal architecture, and not a global catalog; each contract belongs to one module doc set (§6).
- **Preferred naming:** `<Contract>Contract.md` or `<Surface>Contracts.md` inside the owning module's doc set.
- **Example:** `docs/Workflows/SkillAndPlanContracts.md`.

### 3.5 Cross-Cutting Concept View

- **Purpose:** Describe a concept or policy that spans multiple modules, such as security posture, observability, error/redaction policy, or the durable-execution model.
- **Owns:** The concept and the rules that apply across modules. It sets shared expectations that module views and contracts conform to without owning any module's internals.
- **Preferred naming:** A concept-named document under a concern directory, e.g. `docs/Security/<Concept>.md`.
- **Example:** Posture documents under `docs/Security/`, or `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` where it sets execution-boundary rules for all runtimes.

---

## 4. Imperative Working Document Types

Imperative working documents are not part of the canonical Architecture Description. They are time-bound and live outside the canonical tree. This standard names four types so working material is labeled consistently and is not mistaken for architecture.

Canonical views describe *what the system is and should be*; imperative documents describe *steps to get there*. They are kept separate because decomposing an imperative document as if it were a design produces stories that mistake process steps for requirements (Document Model, classification rule). When only an imperative document exists, the underlying declarative view is written or identified first.

| Type | Owns | Does not own | Placement |
|------|------|--------------|-----------|
| **Migration Plan** | Sequenced, phased steps from a current state to a target state, including cutover and backfill ordering. | The target state itself (canonical view) and day-to-day build steps. | `docs/tmp/` or a gitignored handoff path; deleted on completion. |
| **Implementation Plan** | Build tasks, sequencing, and ownership for an already-designed capability. | Design intent and end-state architecture. | `docs/tmp/` or a gitignored handoff path. |
| **Rollout Plan** | Deployment, enablement, flag-flip, and operational sequencing for releasing a change. | The product behavior being rolled out and the build tasks. | `docs/tmp/` or a gitignored handoff path. |
| **Status / Checklist Tracker** | Live status: checkboxes, progress, open items, and assignments for in-flight work. | Any durable desired state. | `docs/tmp/`, `artifacts/`, or a gitignored handoff path; deleted on completion. |

When an effort completes, its working document is deleted rather than left as obsolete plan or status sections inside canonical files. A canonical view with open work may point to a plan or tracking issue but stays readable without it.

---

## 5. Module Boundary Policy

Module directories in the docs tree are **architectural boundaries** or **ownership surfaces**, not domain-modeling claims by default. A module doc set exists because a body of structure or responsibility is owned together.

### 5.1 Default classification: architectural boundary / ownership surface

By default each module directory is either an **architectural boundary** (a unit of structure with a coherent internal architecture and a defined interface) or an **ownership surface** (code/behavior owned and evolved together, even if technical rather than domain-shaped). This covers technical subsystems, framework modules, integrations, plugins, and cross-cutting concerns, which are not Bounded Contexts.

### 5.2 Subtype: Bounded Context

**Bounded Context** is a subtype of architectural boundary, used only where a boundary owns a distinct domain model with its own ubiquitous language. All three tests pass:

1. **Distinct domain model** — the boundary owns its own entities/aggregates and their meanings, not merely a set of functions.
2. **Ubiquitous language** — terms inside the boundary have a precise local meaning that may differ elsewhere.
3. **Translation at the edge** — crossing the boundary requires explicit translation (an anti-corruption layer or equivalent).

If any test fails, the directory is an architectural boundary or ownership surface. Calling a Temporal runtime layer, UI component library, Jira/GitHub adapter, or security concern a Bounded Context inflates the term and erases the signal it carries.

---

## 6. Contracts Are Module-Owned Document Types

A **contract** (Module Contract Specification, §3.4) lives inside the owning module's doc set rather than in a separate global "contracts" area.

### 6.1 Contract authority rule

Each contract is owned by the module that *provides* the interface, and that module's doc set is its single home. When a contract's behavior and documentation disagree, the owning contract document is reconciled with verified implementation rather than duplicated or forked elsewhere.

### 6.2 Cross-module assignment and linking rule

Consumers link to the provider's contract document rather than copying it. A consumer doc may describe *how it uses* the contract, but the interface definition has one source of truth. When a contract spans a provider and well-defined consumers, the specification lives with the provider and consumers add back-links. Linking rather than duplication keeps the contract single-sourced and prevents drift.

---

## 7. Canonical-vs-Canonical Precedence

The Document Model and `AGENTS.md` cover **cross-class** precedence: canonical declarative documents win over derived and imperative artifacts. This section covers the **same-class** case: which canonical declarative document controls when two disagree.

Such conflicts — for example, a system view and a module view describing dependency direction differently, or a design view and a contract specification describing a DTO differently — are resolved **by authority scope, not by file age, edit recency, length, or convenience.** The document whose scope owns that kind of claim is authoritative, and the other is corrected.

### 7.1 Precedence ladder

Higher levels govern broader desired state; lower levels specialize within those bounds. The higher document wins **for claims within its scope**, and the lower document is reconciled to match.

1. **Agent instructions / Document Model** — `AGENTS.md` and `docs/Workflows/MoonSpecDocumentModel.md`: principles, document classification, and cross-class precedence.
2. **Documentation Architecture Standard** — this document: how canonical documents relate, conflict handling, and rationale placement.
3. **System Architecture View** — system-wide structure, the orchestration model, cross-module boundaries, and dependency direction.
4. **Cross-Cutting Concept View** — system-wide semantics of a concern (security, observability, artifact model, skill runtime, Temporal determinism) wherever it appears.
5. **Module Architecture View** — a module's internal structure within the boundaries the system view sets.
6. **Module Contract Specification** — the exact shape and semantics of what crosses a module boundary (DTOs, payload schemas, activity/signal/update signatures, API surfaces).
7. **System/Feature Design View** — feature-level composition and behavior, bounded by the contracts it consumes.
8. **Migration / Implementation / Rollout / Status documents** — sequencing and status, kept in `docs/tmp/` or gitignored handoffs. These describe *how and when* desired state is reached and do not govern desired state itself, so a migration document that contradicts the target architecture is wrong about the target.

Authority follows ownership of the claim type, not blanket dominance. A system view does not override a contract specification on a DTO's layout, because DTO shape is level 6's scope; the system view governs whether that module may *depend on* another module (level 3).

### 7.2 Conflict-handling procedure

1. **Identify the claim type** — dependency direction, DTO/payload shape, cross-cutting semantic, feature behavior, rationale, or sequencing/status.
2. **Identify the owning scope** on the ladder (e.g. cross-module dependency direction → System Architecture View; DTO shape → Module Contract Specification).
3. **Treat the owner as authoritative** and change the non-owning document, regardless of which was edited more recently.
4. **Reconcile in the same change when feasible**, so canonical documentation does not knowingly ship self-contradictory.
5. **Escalate foundational conflicts.** If reconciling would change `AGENTS.md`, the Document Model, this standard, or a published contract that other documents depend on, the conflict is raised with the operator rather than resolved unilaterally.

### 7.3 Worked examples

- **Module view vs system view — dependency direction.** A module view says the module calls another module; the system view forbids that direction. Cross-module dependency direction belongs to the System Architecture View (level 3), so the module view is corrected.
- **Design view vs contract spec — DTO shape.** A design view shows a different field set than the contract specification. DTO shape belongs to the Module Contract Specification (level 6), so the design view is reconciled to the contract's actual shape.
- **Rationale vs normative rule.** A "why" explanation ("for performance we sometimes inline") is read as permission to violate a layering rule stated elsewhere. Rationale explains a rule without granting exceptions, so the rule in the owning document stands and the rationale is clarified.
- **Migration "support both paths" vs target architecture.** A rollout document says the system supports old and new paths; the target architecture describes only the new path. Migration documents are level 8 and do not govern desired state, so dual-path support is at most a time-bound note outside the canonical tree, removed when the migration completes.

---

## 8. Embedded Design-Rationale Policy

Rationale — *why* the desired state is shaped as it is — is part of the canonical record. A reader resolving a conflict or evaluating a change can understand both the rule and its justification from the same document that owns the rule.

Significant design rationale is embedded in the owning architecture, design, or contract document: rationale for a system boundary in the System Architecture View, for a DTO's shape in its contract specification, for a feature's composition in its design view. Two placement forms are recommended:

- **`## Design Rationale`** — a top-level section collecting the document-wide "why" behind several material decisions.
- **`### Rationale`** — a localized subsection immediately after the rule or decision it explains.

Keeping rationale inside the canonical document means it cannot drift from its rule and is updated in the same change when the rule changes. Rationale is explanatory, not normative (see §7.3).

### 8.1 No Separate Design-Rationale Journals

MoonMind does not maintain a standalone design-rationale journal or parallel rationale directory, because it would be a second source of truth that drifts from the canonical document. Readers do not need a separate document to understand current desired state or its design rationale. Time-bound design decisions made during a migration stay with the migration material and are deleted when the work completes.

This applies to durable design rationale only. Operational records that are time-bound *run evidence* — for example, the remediation record in `docs/Workflows/WorkflowRemediation.md`, which captures per-run repair choices — record what happened during a run, not the canonical *why*, and are governed by their owning workflow documents and the Document Model's temporary-execution-artifact class.

---

## 9. Adaptable Examples for Downstream Projects

Downstream MoonSpec projects inherit this standard with minor local adjustments, most commonly docs-root capitalization. The taxonomy is the same either way.

### 9.1 `docs/` form (lowercase root)

```
docs/
  DocumentationArchitecture.md            # this standard (project copy)
  MoonMindArchitecture.md                 # System Architecture View
  <Feature>Design.md                      # System / Feature Design View (Status: Accepted)
  Workflows/                              # module doc set (architectural boundary)
    WorkflowsModuleArchitecture.md         # Module Architecture View
    SkillAndPlanContracts.md               # Module Contract Specification (owned here)
  Security/                               # cross-cutting concern doc set
    OutboundScanPolicy.md                  # Cross-Cutting Concept View
  tmp/                                    # imperative working documents (time-bound)
    <Feature>MigrationPlan.md              # Migration Plan
    <Feature>RolloutPlan.md                # Rollout Plan
```

### 9.2 `Docs/` form (capitalized root)

```
Docs/
  DocumentationArchitecture.md            # this standard (project copy)
  SystemArchitecture.md                   # System Architecture View
  Features/
    <Feature>Design.md                     # System / Feature Design View (Status: Proposed)
  Billing/                                # module doc set — Bounded Context (passes boundary tests)
    BillingModuleArchitecture.md           # Module Architecture View
    BillingApiContract.md                  # Module Contract Specification (owned here)
  Observability/                          # cross-cutting concern doc set
    TelemetryConcepts.md                   # Cross-Cutting Concept View
  Tmp/                                    # imperative working documents (time-bound)
    <Feature>ImplementationPlan.md         # Implementation Plan
    <Feature>StatusTracker.md              # Status / Checklist Tracker
```

In both forms the five viewpoints classify every canonical document, contracts live in their owning module doc set, `Billing/` is a Bounded Context only because it passes §5.2, and imperative documents are confined to the time-bound `tmp/` / `Tmp/` area.

---

## 10. How to Apply This Standard

1. **Classify before writing** — canonical view (one of five viewpoints) or imperative working document (one of four types, placed under `docs/tmp/` or a gitignored handoff path).
2. **Pick the viewpoint** and follow its owns/naming guidance.
3. **Place contracts with their provider**; consumers link rather than copy.
4. **Apply the boundary tests before saying "Bounded Context."**
5. **Promote finished designs** into an architecture view and supersede the design.
6. **Resolve same-class conflicts by authority scope** using §7.1 and §7.2.
7. **Keep rationale with its rule** (§8).

The authoring conventions below — metadata headers (§11), naming (§12), incremental adoption (§13), stable claim IDs (§14), and templates (§15) — build on this taxonomy.

---

## 11. Metadata Headers

A metadata header is a short, human-readable block at the top of a file that classifies the document and records the context a reader needs. There are two shapes: one for canonical declarative documents and one for imperative working documents.

### 11.1 Canonical declarative doc metadata header

New canonical declarative documents carry:

- **Document Class** — `Canonical declarative` (from the [MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md)).
- **Status** — e.g. `Current`, `Draft`, or `Superseded`.
- **Updated** — date of last meaningful revision (`YYYY-MM-DD`).
- **Audience** — who the document is for.
- **Authority** — the slice of architecture, contracts, or behavior the document owns.
- **Owning Surface** — the system, module, or surface the document describes.
- **Related Docs** — adjacent canonical documents.
- **Related Implementation** — the implementing code or modules.

Example:

```markdown
**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-06-24
**Audience:** Contributors and runtime authors
**Authority:** Target semantics for the managed-runtime launcher
**Owning Surface:** moonmind/workflows/temporal/runtime/
**Related Docs:** [MoonMind Architecture](MoonMindArchitecture.md)
**Related Implementation:** ManagedRuntimeLauncher
```

### 11.2 Imperative working document header

Imperative working documents live under `docs/tmp/` or gitignored handoff paths and carry a header that makes their time-bound nature explicit:

- **Document Class** — `Imperative working document` (reserve "plan" for the concrete type or filename).
- **Status** — e.g. `Active`, `Blocked`, or `Done`.
- **Canonical Target** — the canonical document(s) whose desired state the work moves toward.
- **Delete/Archive Trigger** — the concrete condition under which the document is deleted.

Example:

```markdown
**Document Class:** Imperative working document
**Status:** Active
**Canonical Target:** [Documentation Architecture Standard](../DocumentationArchitecture.md)
**Delete/Archive Trigger:** Delete when the authoring conventions land and the standard is published.
```

### 11.3 Optional rationale section

Either header may be followed by a short optional rationale explaining why the document exists or why a non-obvious classification or target was chosen. It is omitted when the header already makes the purpose clear.

---

## 12. Naming Conventions

New documents use descriptive PascalCase filenames from a small preferred set, so a reader can infer a document's role from its name:

| Filename suffix | Use for |
|---|---|
| `<Topic>Architecture.md` | Top-level or subsystem architecture (System Architecture View, §3.1). |
| `<ModuleName>ModuleArchitecture.md` | Preferred filename for a single module's architecture (§3.2). |
| `<SystemName>System.md` | A durable system or capability description once its target model, contracts, and operator-visible behavior are settled. |
| `<FeatureName>Design.md` | A transitional design (§3.3), promoted or superseded once settled. |
| `<Topic>Contract.md` | A declarative contract (§3.4). |
| `<Topic>Plan.md` (under `docs/tmp/`) | A time-bound imperative working document; `Plan`-named documents live under `docs/tmp/`, not the canonical root. |

### 12.1 Capitalization: `docs/` and `Docs/`

The conventions are the same whether the directory is `docs/` (as here) or `Docs/` (as some downstream projects use).

### 12.2 Filename alone does not define authority

What a document is authoritative for comes from its Document Class, its declared Authority, and its place on the precedence ladder (§7), not its filename. A file named `...Architecture.md` is not canonical because of its name, and renaming a file does not change what it governs.

Two patterns are avoided:

- **Parallel old/new authorities.** A topic has one authoritative canonical document. A superseded document is removed in the same change that establishes its replacement, so readers are never left choosing between two.
- **A global `contracts/` directory.** Contracts live in topic-owned `<Topic>Contract.md` files in the owning module's doc set (§6), so a global directory would compete with the owning documents.

---

## 13. Incremental Adoption Policy

The standard is adopted incrementally, without a retroactive rename or metadata-only backfill.

- **New and substantially edited docs first.** Headers, naming, and authority rules apply to new or substantially rewritten documents; others are brought into conformance as they are meaningfully revised.
- **Metadata where it adds value.** Light edits do not trigger a header requirement, and no metadata-only churn change is expected.
- **No forced rename.** Existing filenames remain valid; preferred names are used for new documents and substantial restructures.

### 13.1 Downstream MoonSpec projects

Downstream projects may make minor local adjustments — `Docs/` capitalization, an extra filename suffix, project-specific header fields — while keeping the MoonSpec document classes, the classification rule, the precedence rule, and the rule that filenames do not define authority.

---

## 14. Stable Canonical Claim IDs

Stable canonical claim IDs make durable documentation addressable for indexing, slicing, verification, and reconciliation without depending on run-local coverage IDs.

A claim ID identifies one durable claim in a canonical declarative document. It goes at the start of the smallest heading that owns the claim, with the body explaining the rule, contract, invariant, quality bar, non-goal, or test expectation. The ID survives ordinary edits: rewording, splitting paragraphs, or clarifying examples does not require a new ID while the same claim remains in force.

| Prefix | Use for |
|---|---|
| `DOC-REQ` | Documentation or product behavior requirements a canonical doc owns. |
| `CONTRACT` | API, payload, interface, workflow/activity, signal/update, or provider-boundary contract clauses. |
| `INV` | Invariants that hold across implementation and operations. |
| `NON-GOAL` | Explicit exclusions that prevent scope expansion or misreading as supported behavior. |
| `QUALITY` | Quality attributes such as reliability, performance, observability, security, maintainability, or operator ergonomics. |
| `TEST` | Verification obligations tied to the claim, especially boundary or regression coverage. |

IDs use `PREFIX-NNN` with a zero-padded three-digit number, for example:

- `### DOC-REQ-001 One active skill set is resolved per run`
- `### CONTRACT-001 Activity payloads carry artifact refs for large content`
- `### INV-001 Workflow code stays deterministic`
- `### NON-GOAL-001 Plans do not become canonical desired state`
- `### QUALITY-001 Live-log failure degrades to artifact replay`
- `### TEST-001 Workflow-boundary tests cover real invocation shape`

Canonical claim IDs differ from temporary `DESIGN-REQ-*` IDs, which are run-local or source-design coverage markers used while a design is decomposed and implemented. `DESIGN-REQ-*` values are not stable anchors and are not used as claim IDs; when such a requirement becomes durable desired state, it receives a canonical claim ID and the source value may remain as traceability prose.

Adoption is incremental: new docs and substantially edited sections add IDs for claims they introduce or revise. The advisory checker (`tools/check_documentation_architecture.py`) warns about malformed claim IDs or one ID reused for several claims; warnings stay advisory unless a caller opts into `--strict`.

---

## 15. Viewpoint Templates

Each template embeds the canonical metadata header (§11.1) with a `Viewpoint:` field and a concise section skeleton. Templates are starting points; sections that do not apply are deleted.

- [System Architecture View](./_viewpoints/SystemArchitectureView.template.md) — §3.1
- [Module Architecture View](./_viewpoints/ModuleArchitectureView.template.md) — §3.2
- [System / Feature Design View](./_viewpoints/SystemFeatureDesignView.template.md) — §3.3
- [Module Contract Specification](./_viewpoints/ModuleContractSpecification.template.md) — §3.4
