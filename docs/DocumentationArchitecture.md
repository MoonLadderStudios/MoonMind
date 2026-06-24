# MoonSpec Documentation Architecture Standard

**Status:** Current standard and target direction
**Updated:** 2026-06-24
**Audience:** Anyone authoring, classifying, reviewing, or reorganizing durable documentation in a MoonSpec project
**Purpose:** Establish the single canonical taxonomy that names the document types a MoonSpec durable docs tree uses — so that anyone classifying or writing a doc has one authoritative vocabulary, one set of viewpoints, and one module-boundary policy to apply.

> **Traceability:** This standard is the core deliverable of **MM-902** (source design **MM-900**, "Implement MoonSpec Documentation Architecture Standard"). It covers DESIGN-REQ-001 through DESIGN-REQ-007 and DESIGN-REQ-013. Precedence/rationale, metadata/naming, integration, templates, migration, and validation are split into dependent stories and are intentionally out of scope here.

---

## 1. Scope and Relationship to the MoonSpec Document Model

This document is a **MoonSpec-level strategy**: it describes the desired-state shape of a durable documentation tree, not a migration plan or a checklist for any one repository.

**It extends, and does not replace, [`docs/Workflows/MoonSpecDocumentModel.md`](Workflows/MoonSpecDocumentModel.md).**

The Document Model is the foundation. It defines the three operative **document classes** (canonical declarative documents, temporary execution artifacts, imperative working documents), the **classification rule** (declarative vs. imperative by primary framing), the **precedence rule** (canonical wins; derived artifacts never self-resolve conflicts), and the **reconciliation expectation** (canonical docs are realigned with verified implementation through doc reconciliation). Those rules remain authoritative and are not restated or overridden here.

This standard layers a finer-grained **architectural taxonomy** on top of that base:

- It names the **umbrella vocabulary** a project uses to talk about its documentation tree as an architecture description.
- It enumerates the **canonical declarative viewpoints** that subdivide the Document Model's "canonical declarative documents" class.
- It names the **imperative working document types** that subdivide the Document Model's "imperative working documents" class.
- It defines the **module boundary policy** and the **module-owned contract** document type.

Where this standard and the Document Model could appear to disagree, the Document Model governs the underlying class, precedence, and reconciliation rules; this standard governs only the finer vocabulary and viewpoint taxonomy built on top of them. New terms introduced here are additive refinements of the base classes, never substitutes for them.

---

## 2. Umbrella Vocabulary

These are the **local MoonSpec terms** every author should use when discussing the documentation tree. They are deliberately small and stable.

| Term | Definition |
|------|------------|
| **Architecture Description** | The whole documentation tree of a project, treated as one coherent description of the system's architecture. It is the set of all canonical declarative views plus the module doc sets, governed by this standard. There is one Architecture Description per project. |
| **Viewpoint** | A *kind* of canonical declarative document — a reusable template defining a purpose, what such a document owns, its preferred naming, and the concerns it addresses. A viewpoint is the schema; a view is an instance. This standard defines five canonical declarative viewpoints. |
| **View** | A *concrete document* that conforms to a viewpoint. "System Architecture View" is a viewpoint; `docs/MoonMindArchitecture.md` is a view that conforms to it. A view is always declarative and desired-state (per the Document Model). |
| **Module Doc Set** | The collection of canonical documents owned by a single module directory (an architectural boundary or ownership surface). A module doc set holds that module's Module Architecture View, any Module Contract Specifications it owns, and module-scoped design or cross-cutting views. Contracts live *inside* the owning module doc set, never in a global documentation area. |
| **Canonical Declarative View** | Shorthand for any view: a long-lived, version-controlled document under `docs/` that describes desired state (architecture, contracts, operator-visible behavior, target semantics). Synonymous with the Document Model's "canonical declarative document," scoped to a viewpoint. |
| **Imperative Working Document** | A time-bound document whose primary framing is steps, phases, checkboxes, or status (a Migration Plan, Implementation Plan, Rollout Plan, or Status/Checklist Tracker). Identical to the Document Model's "imperative working documents" class; this standard only names the four concrete types. |

The first four terms (Architecture Description, Viewpoint, View, Module Doc Set) are the architectural backbone; the last two (Canonical Declarative View, Imperative Working Document) are the bridge terms that tie this vocabulary back to the Document Model's classes.

---

## 3. Canonical Declarative Viewpoints

A MoonSpec Architecture Description is composed of views conforming to exactly **five canonical declarative viewpoints**. Every canonical declarative document under `docs/` should be classifiable as one of these. All five are declarative desired-state documents and are subject to the Document Model's precedence and reconciliation rules.

For each viewpoint below: **Purpose** says why it exists, **Owns** says what content is authoritative there (and, by exclusion, what belongs elsewhere), **Preferred naming** gives the canonical title/path convention, and **Example** gives at least one concrete instance.

### 3.1 System Architecture View

- **Purpose:** Describe the system as a whole — its major components, their responsibilities, the runtime/orchestration model, and how the parts fit together at the top level.
- **Owns:** System-wide structure, cross-component data and control flow, top-level invariants, and the architectural direction of the system as a single thing. It does *not* own the internal structure of any one module (that is a Module Architecture View) nor any single contract (that is a Module Contract Specification).
- **Preferred naming:** `Architecture.md` at the docs root, or `<System>Architecture.md` (e.g. `MoonMindArchitecture.md`).
- **Example:** `docs/MoonMindArchitecture.md`.

### 3.2 Module Architecture View

- **Purpose:** Describe the internal architecture of a single module (an architectural boundary or ownership surface): its responsibilities, internal structure, and how it relates to neighboring modules.
- **Owns:** That module's internal component model, its responsibilities, and the rationale for its boundary. It does *not* own system-wide structure (System Architecture View) and does *not* own the formal interface another module depends on (that is a Module Contract Specification within this module's doc set).
- **Preferred naming:** An `Architecture.md` or `Overview.md` inside the module's doc directory, e.g. `docs/<Module>/Architecture.md`.
- **Example:** `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` (the execution-model architecture for the Temporal runtime boundary).

### 3.3 System / Feature Design View

- **Purpose:** Describe a proposed or in-progress design for a system-level capability or feature before (and as) it becomes settled architecture. This is where new design work lives until it is mature enough to be promoted into a System Architecture View or a Module Architecture View.
- **Owns:** The design intent, the considered options and the chosen approach, and the desired behavior of the feature. It does *not* own the imperative plan to build it (that is an Implementation Plan) and does *not* permanently own settled architecture once the design is realized.
- **Preferred naming:** `<Feature>Design.md`, placed at the system docs root for system-level features or inside the owning module doc set for module-scoped features.
- **Status field:** Every System / Feature Design View **must** carry a `Status:` field near the top — one of `Proposed`, `Accepted`, `Implemented`, or `Superseded` — so readers can tell design intent from realized architecture at a glance.
- **Design → System promotion rule:** When a design reaches `Implemented` and its content is durable desired state, its settled architectural substance **must be promoted** into the appropriate System Architecture View or Module Architecture View, and the Design View is then marked `Superseded` (and pointed at its successor) or removed. A System / Feature Design View is never the permanent home of settled architecture; leaving realized design as the only canonical record is a defect, not a convenience.
- **Example:** A `docs/Workflows/StepReviewGateSystem.md`-style design document carried at `Status: Accepted` while the capability is being designed, later promoted into the relevant architecture view once implemented.

### 3.4 Module Contract Specification

- **Purpose:** Define the formal, depended-upon interface a module exposes — its API/contract surface, payload shapes, invariants, and compatibility expectations.
- **Owns:** The authoritative definition of one module's contract. Consumers cite this document as the source of truth for the interface. It does *not* own the module's internal architecture (Module Architecture View) and is *not* a global catalog — each contract is owned by exactly one module doc set (see §5).
- **Preferred naming:** `<Contract>Contract.md` or `<Surface>Contracts.md`, inside the owning module's doc set.
- **Example:** `docs/Workflows/SkillAndPlanContracts.md` (the contract specification for executable skill/plan tool contracts, owned by the workflows module doc set).

### 3.5 Cross-Cutting Concept View

- **Purpose:** Describe a concept, policy, or concern that legitimately spans multiple modules and cannot be owned by a single module — for example security posture, observability, error/redaction policy, or the durable-execution model.
- **Owns:** The cross-cutting concept itself and the rules that apply across modules. It does *not* claim ownership of any single module's internal architecture or contract; it sets shared expectations that module views and contracts conform to.
- **Preferred naming:** A concept-named document under a concern directory, e.g. `docs/Security/<Concept>.md` or `docs/Observability/<Concept>.md`.
- **Example:** `docs/Security/`-housed posture documents, or `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` where it sets execution-boundary rules that all runtimes obey.

---

## 4. Imperative Working Document Types

Imperative working documents are **not** part of the canonical Architecture Description. They are time-bound, follow the Document Model's "imperative working documents" class, and live outside the canonical desired-state tree. This standard names exactly **four** types so that working material is labeled consistently and is never mistaken for canonical architecture.

> **Separation rule (canonical vs. working):** Canonical declarative views describe *what the system is and should be*; imperative working documents describe *steps to get there*. The two are kept physically and conceptually separate. Imperative content must not become the primary framing of any canonical view (Constitution: *Canonical Documentation Separates Desired State from Migration Backlog*). Decomposing an imperative document as if it were a design produces stories that mistake process steps for requirements (Document Model, classification rule) — so when only an imperative document exists, write or identify the underlying declarative view first.

| Type | Owns | Does not own | Placement |
|------|------|--------------|-----------|
| **Migration Plan** | The sequenced, phased steps to move from a current state to a target state, including cutover and backfill ordering. | The target desired state itself (that belongs in the relevant canonical view) and the day-to-day build steps (Implementation Plan). | `docs/tmp/` or a gitignored handoff path; deleted/archived on completion. |
| **Implementation Plan** | The concrete build tasks, sequencing, and ownership needed to implement an already-designed capability. | The design intent (System / Feature Design View) and the desired end-state architecture (architecture views). | `docs/tmp/`, run-local `specs/<feature>/`, or a gitignored handoff path. |
| **Rollout Plan** | The deployment, enablement, flag-flip, and operational sequencing for releasing a change to environments/users. | The product behavior being rolled out (canonical views) and the build tasks (Implementation Plan). | `docs/tmp/` or a gitignored handoff path. |
| **Status / Checklist Tracker** | Live status: checkboxes, progress, open items, and who-is-doing-what for in-flight work. | Any durable desired state; it is pure status and never a source of truth for behavior. | `docs/tmp/`, `artifacts/`, or a gitignored handoff path; deleted/archived on completion. |

When a migration or implementation effort completes, **delete or archive** the corresponding working document rather than leaving obsolete plan or status sections inside canonical files. A canonical view with open work may *point* to the relevant `docs/tmp/` plan or tracking issue, but must remain readable without it.

---

## 5. Module Boundary Policy

Module directories in the docs tree are **architectural boundaries** or **ownership surfaces**, not domain-modeling claims by default. A module doc set exists because a body of structure or responsibility is owned together — not because the module necessarily encodes a distinct business domain.

### 5.1 Default classification: architectural boundary / ownership surface

By default, treat each module directory as one of:

- an **architectural boundary** — a unit of structure with a coherent internal architecture and a defined interface to its neighbors; or
- an **ownership surface** — a body of code/behavior that is owned and evolved together, even if it is technical rather than domain-shaped.

This default applies to technical subsystems, framework modules, integrations, plugins, and cross-cutting concerns. These are **not** Bounded Contexts and must not be labeled as such.

### 5.2 Subtype: Bounded Context

**Bounded Context** is a *subtype* of architectural boundary, permitted **only** where a boundary genuinely owns a **distinct domain model with its own ubiquitous language**. Use the term only when all of the following boundary tests pass:

1. **Distinct domain model** — the boundary owns its own entities/aggregates and their meanings, not merely a set of functions.
2. **Ubiquitous language** — terms inside the boundary have a precise, boundary-local meaning that may differ from the same word elsewhere in the system.
3. **Translation at the edge** — crossing into or out of the boundary requires explicit translation of concepts (an anti-corruption layer or equivalent), because the models genuinely differ.

If any test fails, the directory is an architectural boundary or ownership surface — not a Bounded Context.

> **Do not over-apply "Bounded Context."** Technical subsystems (e.g. a Temporal runtime layer), framework modules (e.g. a UI component library), integrations (e.g. a Jira or GitHub adapter), plugins, and cross-cutting concerns (e.g. security, observability) are ownership surfaces by default. Calling them Bounded Contexts inflates the term and erases the signal it is meant to carry. Reserve it for true domain boundaries.

---

## 6. Contracts Are Module-Owned Document Types

A **contract** (a Module Contract Specification, §3.4) is a **module-owned document type** that lives **inside the owning module's doc set** — never in a separate, global "contracts" documentation area.

### 6.1 Contract authority rule

Each contract is **owned by exactly one module**: the module that *provides* the interface. That module's doc set is the single authoritative home for the contract's definition. There is no global contracts directory that supersedes module ownership, and no contract has two canonical homes. When a contract's behavior and its documentation disagree, the owning module's contract document is reconciled with verified implementation (Document Model, reconciliation expectation) — it is not duplicated or forked elsewhere.

### 6.2 Cross-module assignment and linking rule

When a contract is *consumed* by other modules:

- The contract is **assigned to the providing module** and documented in that module's doc set. Consumers do **not** copy the contract into their own doc sets.
- Consuming modules **link** to the owning module's contract document rather than restating it. A consumer doc may describe *how it uses* the contract, but the interface definition itself has one source of truth.
- When a contract surface spans a provider and well-defined consumers, place the canonical specification with the provider and add back-links from each consumer's module doc set. Linking, not duplication, keeps the contract single-sourced and prevents drift.

---

## 7. Adaptable Examples for Downstream Projects

Downstream MoonSpec projects inherit this standard with **minor local adjustments** — most commonly the capitalization of the docs root directory. The taxonomy is identical regardless of capitalization; only the path prefix changes. Both forms below are valid; pick the one matching the project's existing tree.

### 7.1 `docs/` form (lowercase root)

```
docs/
  DocumentationArchitecture.md            # this standard (project copy)
  MoonMindArchitecture.md                 # System Architecture View
  <Feature>Design.md                      # System / Feature Design View (Status: Accepted)
  Workflows/                              # module doc set (architectural boundary)
    Architecture.md                        # Module Architecture View
    SkillAndPlanContracts.md               # Module Contract Specification (owned here)
  Security/                               # cross-cutting concern doc set
    OutboundScanPolicy.md                  # Cross-Cutting Concept View
  tmp/                                    # imperative working documents (time-bound)
    <Feature>MigrationPlan.md              # Migration Plan
    <Feature>RolloutPlan.md                # Rollout Plan
```

### 7.2 `Docs/` form (capitalized root)

```
Docs/
  DocumentationArchitecture.md            # this standard (project copy)
  SystemArchitecture.md                   # System Architecture View
  Features/
    <Feature>Design.md                     # System / Feature Design View (Status: Proposed)
  Billing/                                # module doc set — Bounded Context (passes boundary tests)
    Architecture.md                        # Module Architecture View
    BillingApiContract.md                  # Module Contract Specification (owned here)
  Observability/                          # cross-cutting concern doc set
    TelemetryConcepts.md                   # Cross-Cutting Concept View
  Tmp/                                    # imperative working documents (time-bound)
    <Feature>ImplementationPlan.md         # Implementation Plan
    <Feature>StatusTracker.md              # Status / Checklist Tracker
```

In both forms: the five viewpoints classify every canonical declarative document, contracts live inside their owning module doc set, module directories are architectural boundaries/ownership surfaces (with `Billing/` shown as a Bounded Context only because it passes the boundary tests in §5.2), and imperative working documents are confined to the time-bound `tmp/` / `Tmp/` area, separate from the canonical Architecture Description.

---

## 8. How to Apply This Standard

1. **Classify before you write.** Decide whether the document is a canonical declarative view (one of the five viewpoints) or an imperative working document (one of the four types). If imperative, place it under `docs/tmp/` or a gitignored handoff path.
2. **Pick the viewpoint.** For canonical work, choose System Architecture, Module Architecture, System / Feature Design, Module Contract Specification, or Cross-Cutting Concept — and follow that viewpoint's owns/naming guidance.
3. **Place contracts with their owner.** Document a contract inside the providing module's doc set; have consumers link, not copy.
4. **Apply the boundary test before saying "Bounded Context."** Default to architectural boundary / ownership surface; reserve Bounded Context for true domain boundaries that pass all three tests.
5. **Promote finished designs.** When a System / Feature Design View is `Implemented`, promote its settled substance into an architecture view and supersede the design.

This standard is the foundation; precedence/rationale, metadata/naming conventions, integration with tooling, document templates, migration guidance, and validation are defined in dependent stories that build on this taxonomy.
