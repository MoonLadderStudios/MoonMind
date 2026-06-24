# Documentation Architecture Standard

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-06-24
**Audience:** Doc authors, contributors, MoonSpec workflow authors, and downstream MoonSpec projects
**Authority:** Canonical standard for how MoonMind documentation is classified, named, and adopted
**Owning Surface:** Documentation system / MoonSpec document model
**Related Docs:** [MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md), [Constitution](../.specify/memory/constitution.md) (Principle XV)
**Related Implementation:** MM-900 (source standard), MM-904 (authoring conventions)

This standard defines the **authoring conventions** for MoonMind documentation: the lightweight metadata header for new canonical docs, the imperative-plan header, the preferred filenames for new docs, and the incremental adoption policy. It builds on the document classes, classification rule, precedence rule, and reconciliation expectation defined in the [MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md) and the canonical/desired-state separation required by Constitution Principle XV. It does not restate those rules; it tells authors how to apply them when they create or substantially edit a document.

---

## Metadata Headers

A document's metadata header is a short, human-readable block at the top of the file. It classifies the document and records the minimum context a reader needs before trusting it. Two header shapes exist — one for canonical declarative documents, one for imperative plans.

### Canonical declarative doc metadata header

New canonical declarative documents (long-lived files under `docs/` that describe desired state) carry the following fields:

- **Document Class** — `Canonical declarative` (the class from the MoonSpec Document Model).
- **Status** — the document's standing, e.g. `Current`, `Draft`, or `Superseded`.
- **Updated** — the date the document was last meaningfully revised (`YYYY-MM-DD`).
- **Audience** — who the document is written for (operators, contributors, runtime authors, etc.).
- **Authority** — what the document is authoritative for: the slice of architecture, contracts, or behavior it owns.
- **Owning Surface** — the system, module, or surface the document describes and whose changes should keep it current.
- **Related Docs** — links to adjacent canonical documents a reader should know about.
- **Related Implementation** — pointers to the implementing code, modules, or tracking issues that realize the described state.

Example:

```markdown
**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-06-24
**Audience:** Contributors and runtime authors
**Authority:** Target semantics for the managed-runtime launcher
**Owning Surface:** moonmind/workflows/temporal/runtime/
**Related Docs:** [MoonMind Architecture](MoonMindArchitecture.md)
**Related Implementation:** ManagedRuntimeLauncher (MM-XXX)
```

### Imperative-plan header

Imperative working documents (migration plans, rollout sequencing, status trackers, cleanup checklists) live under `docs/tmp/` or gitignored handoff paths per Constitution Principle XV. They carry a different header that makes their time-bound nature and disposal trigger explicit:

- **Document Class** — `Imperative plan`.
- **Status** — e.g. `Active`, `Blocked`, or `Done`.
- **Canonical Target** — the canonical declarative document(s) whose desired state this plan is working toward.
- **Delete/Archive Trigger** — the concrete condition under which this document must be deleted or archived (for example, "delete when MM-904 lands and the standard is published").

Example:

```markdown
**Document Class:** Imperative plan
**Status:** Active
**Canonical Target:** docs/DocumentationArchitecture.md
**Delete/Archive Trigger:** Delete when the authoring conventions land and the standard is published.
```

### Optional rationale section

Either header may be followed by an optional short **rationale** section that records *why* the document exists or why a non-obvious classification, authority boundary, or canonical target was chosen. The rationale is optional; omit it when the header fields already make the document's purpose clear.

---

## Naming Conventions

New documents use descriptive, PascalCase filenames drawn from a small preferred set, so a reader can infer a document's role from its name. The preferred filename set is:

| Filename suffix | Use for |
|---|---|
| `<Topic>Architecture.md` | Top-level or subsystem architecture descriptions. |
| `<ModuleName>ModuleArchitecture.md` | **Preferred** filename for module-architecture documents — the architecture of a single named module. |
| `<Topic>System.md` | A named system or capability (its model, contracts, and operator-visible behavior). |
| `<Topic>Design.md` | A focused design for a feature or mechanism. |
| `<Topic>Contract.md` | A declarative contract (payload shapes, interface guarantees, boundary semantics). |
| `<Topic>Plan.md` (under `docs/tmp/`) | A time-bound imperative plan. `Plan`-named documents belong under `docs/tmp/`, never in the canonical `docs/` root. |

`<ModuleName>ModuleArchitecture.md` is the preferred filename when documenting the architecture of a single module (for example, `RuntimeLauncherModuleArchitecture.md`).

### Capitalization: `docs/` and `Docs/`

The conventions apply identically whether the documentation directory is `docs/` (lowercase, as in this repository) or `Docs/` (capitalized, as some downstream MoonSpec projects use). The directory capitalization is a local choice; the preferred filename set and the rules below are the same in either case.

### Filename alone does not define authority

A filename is a hint, not an authority claim. **What a document is authoritative for is defined by its Document Class, its declared Authority, and its registration in the documentation index — not by its filename.** A file named `...Architecture.md` is not canonical merely because of its name, and renaming a file does not change what it governs.

Two patterns are forbidden:

- **Parallel old/new authorities.** A topic must have exactly one authoritative canonical document. Do not stand up a new authority alongside an old one for the same topic (per the Pre-Release Velocity principle, replace the old document rather than running both). If a document is superseded, mark it `Superseded` and remove it in the same change that establishes the replacement.
- **A global `contracts/` directory.** Contracts are documented in topic-owned `<Topic>Contract.md` files next to the surface they govern, not collected into a single global `contracts/` directory that competes with the owning documents for authority.

---

## Incremental Adoption Policy

This standard is adopted **incrementally**. It does **not** require a large retroactive rename or a metadata-only backfill across existing documentation.

- **New and substantially-edited docs first.** The metadata header, naming conventions, and authority rules apply to documents that are newly created or substantially rewritten. Existing documents are brought into conformance opportunistically, as they are meaningfully revised — not in a separate sweep.
- **Metadata is required only where it adds value.** A metadata header is required for new canonical documents and for substantial rewrites. Light edits to an existing document do not trigger a header requirement, and **no retroactive metadata-only churn PR is mandated**.
- **No forced repo-wide rename.** Existing filenames that predate this standard remain valid; adopt the preferred filenames for new documents and when a document is being substantially restructured anyway.

### Downstream MoonSpec projects

Downstream MoonSpec projects may apply **minor local adjustments** to these conventions — for example, `Docs/` capitalization, an additional locally-meaningful filename suffix, or project-specific header fields — **while preserving the MoonSpec document classes and authority rules**. The document classes (canonical declarative, temporary execution artifact, imperative working document), the classification rule, the precedence rule, and the "filename alone does not define authority" rule are not negotiable; the cosmetic and additive details around them are.
