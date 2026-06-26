# Documentation Architecture Validation (Advisory)

**Document Class:** Cross-Cutting Concept View
**Status:** Current standard and target direction
**Updated:** 2026-06-25
**Audience:** Anyone authoring or reviewing durable documentation, or wiring documentation checks into tooling
**Purpose:** Describe the **advisory, non-blocking** validation that flags obvious drift from the [MoonSpec Documentation Architecture Standard](DocumentationArchitecture.md) and the [MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md), so reviewers catch documentation-architecture problems early.

> **Traceability:** This is the deliverable of **MM-908** (source design **MM-900**, "Implement MoonSpec Documentation Architecture Standard"); it covers **DESIGN-REQ-018**. It builds on the taxonomy authored in **MM-902**. Stable canonical claim ID validation is added under **MM-929**, preserving source issue **MM-927** traceability.

---

## 1. Advisory only in v1

This validation is **advisory only**. It **does not block CI** and is **not** added to any required pipeline in this story. Its job is to surface likely documentation-architecture issues so a human reviewer can decide. Findings are emitted as **structured warnings** (stable rule ids + a `severity` field) precisely so the convention can later be **promoted to a blocking CI gate** — once it proves stable — without reworking callers.

The helper exits `0` regardless of findings. A future promotion can run it with `--strict` (which exits non-zero when findings exist); v1 CI must not.

## 2. The helper

`tools/check_documentation_architecture.py` implements the enumerated checks.

```bash
# Advisory scan of docs changed vs origin/main (default):
python tools/check_documentation_architecture.py

# Scan the whole docs/ tree:
python tools/check_documentation_architecture.py --scope all

# Machine-readable output (structured warnings):
python tools/check_documentation_architecture.py --scope all --format json

# Check specific files:
python tools/check_documentation_architecture.py docs/MyNewView.md
```

By default it scopes to **new/changed** docs (`git diff` vs `--base`, default `origin/main`) so it focuses on what a change introduces; `--scope all` audits the full tree. If git is unavailable it falls back to a full scan.

## 3. What it checks

Each check maps to one acceptance criterion of MM-908 and emits a finding with a stable `rule` id:

| Rule id | Flags | Reference |
|---------|-------|-----------|
| `missing-document-class` | A canonical doc under `docs/` that declares no Document Class (no `Document Class:` marker and no recognized base-class/viewpoint name). | [Standard §3](DocumentationArchitecture.md), [Document Model — Document Classes](Workflows/MoonSpecDocumentModel.md) |
| `imperative-plan-in-canonical-area` | A `*Plan.md` (or `*Tracker.md` / `*Checklist.md` / `*Backlog.md`) placed in a canonical folder instead of `docs/tmp/` (or another approved imperative working area). | [Standard §4](DocumentationArchitecture.md) |
| `duplicate-canonical-authority` | Two canonical docs sharing an H1 title (overlapping authority), or more than one root-level System Architecture View. | [Standard §3.1](DocumentationArchitecture.md), [Document Model — Precedence](Workflows/MoonSpecDocumentModel.md) |
| `contract-missing-authority-statement` | A contract doc (`*Contract.md` / `*Contracts.md`) with no authority statement naming its single authoritative owner / source of truth. | [Standard §6.1](DocumentationArchitecture.md) |
| `discouraged-decision-record` | A separate `decisions/` directory or ADR-style doc, introduced instead of embedding rationale in the owning canonical view. | [Standard §8](DocumentationArchitecture.md) |
| `malformed-claim-id` | A canonical claim heading using a malformed stable ID. | [Standard §14](DocumentationArchitecture.md) |
| `duplicate-claim-id` | A stable canonical claim ID reused for multiple canonical claims. | [Standard §14](DocumentationArchitecture.md) |

## 4. Reviewer checklist

When reviewing a change that adds or moves durable docs, confirm:

- [ ] Each new canonical doc names its **Document Class** (one of the Document Model classes or one of the five Standard viewpoints).
- [ ] Plans, rollout/migration plans, and status/checklist trackers live under `docs/tmp/` (or a gitignored handoff path), **not** in a canonical architecture folder, and are not named `*Plan.md` under `docs/`.
- [ ] No two canonical docs claim the **same authority** (same title / same scope); there is one root-level System Architecture View.
- [ ] Each **contract** doc states who **owns** it and that it is the **single source of truth** for that interface.
- [ ] Rationale is **embedded** in the owning canonical view rather than added as a separate `decisions/` or ADR-style doc.
- [ ] Stable canonical claim headings use `PREFIX-NNN` with one of `DOC-REQ`, `CONTRACT`, `INV`, `NON-GOAL`, `QUALITY`, or `TEST`, and no stable ID is reused for a different canonical claim.

## 5. Document Class marker convention

To satisfy `missing-document-class`, a canonical doc should carry a near-the-top marker, for example:

```markdown
**Document Class:** Module Contract Specification
```

The recognized values are the Document Model base classes (canonical declarative document, temporary execution artifact, imperative working document) and the five Standard viewpoints (System Architecture View, Module Architecture View, System / Feature Design View, Module Contract Specification, Cross-Cutting Concept View). A doc that names its viewpoint inline already satisfies the check.
