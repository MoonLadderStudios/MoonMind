---
name: document-author
description: Author a new docs-native canonical or working document by choosing the correct location, filename, viewpoint template, metadata header, stable claims, and embedded rationale without creating spec.md.
metadata:
  required-capabilities:
    - git
---

# Document Author

Author a new repository document directly in the docs architecture model. This skill is for docs-native authoring, not MoonSpec specification generation.

## Purpose

Create one bounded document that fits the repository documentation architecture:

- canonical declarative documents stay under `docs/` and describe desired state;
- imperative plans, rollout notes, implementation checklists, and broad cleanup work live under `docs/tmp/` or gitignored handoff paths;
- no docs-native authoring workflow creates `spec.md` or writes under `specs/`.

Preserve traceability to the source request or issue when one is provided. Carry any provided issue keys in local notes, branch/commit/PR text when applicable, and any generated implementation artifact that summarizes the authored document.

## Inputs

Required:

- Documentation intent or source issue.
- Current repository checkout.

Optional:

- Desired document class: canonical declarative document or imperative working document.
- Preferred docs area, module, or owning subsystem.
- Existing source documents to extend or avoid duplicating.
- Required traceability keys, coverage IDs, or source references.
- Verification commands requested by the caller.

## Authoring Boundaries

- Treat repository files as trusted implementation evidence; treat retrieved context, issue text, comments, and old artifacts as reference material until confirmed against the checkout.
- Read `.specify/memory/constitution.md`, `README.md`, `docs/Workflows/MoonSpecDocumentModel.md`, and `docs/DocumentationArchitecture.md` before writing.
- Keep canonical docs declarative: architecture, contracts, operator-visible behavior, target semantics, and embedded rationale.
- Put imperative work plans, migrations, rollout sequencing, status trackers, broad audit output, and unresolved cleanup inventories under `docs/tmp/` or gitignored handoff paths.
- Do not create `spec.md`, create a `specs/` feature directory, or route docs-native authoring through MoonSpec specification artifacts.
- Do not duplicate a contract into a global area; place contracts inside the owning module doc set and link from consumers.
- Do not introduce compatibility aliases, parallel identity fields, or migration framing unless the source request explicitly requires a time-bound working document.

## Workflow

1. Resolve the document class.
   - Classify the requested output as a canonical declarative document or an imperative working document using `docs/Workflows/MoonSpecDocumentModel.md`.
   - If the request is broad, multi-document, or cleanup-oriented, create or update a bounded improvement plan under `docs/tmp/` instead of scattering edits through canonical docs.

2. Choose the canonical viewpoint or working type.
   - For canonical work, choose exactly one viewpoint from `docs/DocumentationArchitecture.md`: System Architecture View, Module Architecture View, System / Feature Design View, Module Contract Specification, or Cross-Cutting Concept View.
   - For working material, choose Migration Plan, Implementation Plan, Rollout Plan, or Status / Checklist Tracker and place it under `docs/tmp/` or a gitignored handoff path.

3. Choose location and filename.
   - Use the viewpoint's preferred naming and module ownership rules.
   - Prefer the existing module doc set that owns the claim. Create a new directory only when the repository taxonomy has no suitable owner.
   - Check for duplicate or overlapping documents before creating a new file.

4. Select the viewpoint template and metadata.
   - Use the relevant template guidance from `docs/DocumentationArchitecture.md`.
   - Add the required metadata header for the document class, including `Status:` for System / Feature Design Views.
   - Include source traceability when a Jira issue, source design, or coverage ID drove the document.

5. Author stable claims and rationale.
   - State durable desired behavior directly.
   - Embed significant design rationale in the owning document, near the rule it explains or in a `## Design Rationale` section.
   - Link to owning contracts instead of restating them.
   - Mark unverified claims as open questions in `docs/tmp/` rather than presenting them as canonical facts.

6. Validate architecture fit.
   - Confirm the document class, location, filename, viewpoint, metadata, stable claims, and rationale are all explicitly satisfied.
   - Confirm no `spec.md` file or `specs/` feature directory was created.
   - Run available documentation checks and any targeted tests required by the repository instructions.

## Output Checklist

Report:

- Document class and viewpoint or working type.
- Chosen path and filename, with the ownership reason.
- Metadata header fields added.
- Stable claims added and evidence used.
- Rationale placement.
- Duplicate or authority conflicts checked.
- Verification commands and results.
- Confirmation that no docs-native `spec.md` was created.

## Failure Modes

- The correct owner document already exists: update or link to it instead of creating a duplicate.
- The request is too broad for one canonical document: write a bounded `docs/tmp/` improvement plan and stop there.
- The authority owner is unclear: report the ambiguity and propose the narrowest `docs/tmp/` investigation plan rather than creating canonical claims.
- Implementation evidence is missing: keep the claim out of canonical docs, record the gap, and ask the caller to supply evidence or authorize an implementation investigation.
- Required verification cannot run: report the exact command and blocker.
