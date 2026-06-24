# MoonSpec Documentation Architecture Standard

**Status:** Active
**Document Class:** Canonical declarative
**Owner:** MoonMind Engineering
**Last Updated:** 2026-06-24
**Audience:** Anyone authoring or reviewing documentation under `docs/`
**Related:** `.specify/memory/constitution.md` (Principles XII, XV), `docs/Workflows/MoonSpecDocumentModel.md`
**Source:** MM-900 (Implement MoonSpec Documentation Architecture Standard); template deliverable tracked by MM-906

This standard defines the **viewpoints** MoonMind documentation is written against and the **metadata headers** every document must carry. It complements `docs/Workflows/MoonSpecDocumentModel.md`, which defines the underlying document classes (canonical declarative, temporary execution artifact, imperative working document) and their precedence rules — this standard does not restate those rules.

## Viewpoints

A *viewpoint* answers one question about the system at one altitude. Pick the single viewpoint that matches what you are documenting; do not blend them in one file.

| Viewpoint | Answers | Document class | Header |
|-----------|---------|----------------|--------|
| **System Architecture View** | How do the major components fit together across the whole system? | Canonical declarative | Canonical metadata header |
| **Module Architecture View** | How is one module/subsystem structured internally? | Canonical declarative | Canonical metadata header |
| **System / Feature Design View** | What is the intended behavior and shape of a system or feature? | Canonical declarative | Canonical metadata header |
| **Module Contract Specification** | What interface/contract does a module expose and guarantee? | Canonical declarative | Canonical metadata header |
| **Migration / Implementation Plan** | What steps move us from current to desired state? | Imperative working document | Imperative-plan header |

The first four are **canonical viewpoints**: they describe desired state and live durably under `docs/`. The Migration / Implementation Plan is **imperative**: it describes steps, phases, or status and lives under `docs/tmp/` or a gitignored handoff path, to be deleted or archived when the work completes (Constitution XII / XV).

## Metadata headers

Every document opens with a metadata header immediately after its `#` title.

### Canonical metadata header

Used by the four canonical viewpoints.

```
**Status:** <Draft | Active | Implemented>
**Document Class:** Canonical declarative
**Viewpoint:** <System Architecture View | Module Architecture View | System / Feature Design View | Module Contract Specification>
**Owner:** <team or person>
**Last Updated:** <YYYY-MM-DD>
**Audience:** <primary readers>
**Related:** <links to related canonical docs and/or source issues>
```

### Imperative-plan header

Used by the Migration / Implementation Plan only.

```
**Status:** <proposal | in progress | done> (<YYYY-MM-DD>)
**Document Class:** Imperative working document
**Location policy:** docs/tmp/ or gitignored handoff path (delete or archive on completion)
**Owner:** <person driving the work>
**Last Updated:** <YYYY-MM-DD>
**Tracks:** <canonical doc(s) and/or issue this plan executes against>
```

## Viewpoint templates

Copy the matching template to start a new document. Each template embeds the correct header and a concise section skeleton drawn from this standard. Templates are starting points, not a heavyweight process: delete sections that do not apply.

- [System Architecture View](./_viewpoints/SystemArchitectureView.template.md)
- [Module Architecture View](./_viewpoints/ModuleArchitectureView.template.md)
- [System / Feature Design View](./_viewpoints/SystemFeatureDesignView.template.md)
- [Module Contract Specification](./_viewpoints/ModuleContractSpecification.template.md)
- [Migration / Implementation Plan](./_viewpoints/MigrationImplementationPlan.template.md)
