# Skill Required Capabilities Hardening Plan

Status: proposal (2026-06-17). Execution plan — lives in `docs/tmp/` because this is rollout and migration work, not the canonical desired-state Skill System contract.

## Goal

Make selected agent Skills declare, propagate, and enforce the runtime capabilities they need, so a directly selected Skill gets the same access preparation as a preset-backed workflow.

This plan intentionally does **not** introduce a new GitHub/Jira access system. The existing `requiredCapabilities` plumbing remains the control-plane and execution-plane contract; the gap is that Skill selection does not yet reliably declare and enforce those requirements.

## Current-state findings

- Advanced mode can already add per-skill `requiredCapabilities`, such as `git` and `jira`.
- The Create page already derives top-level `payload.requiredCapabilities` from runtime mode, publish mode, task Skill capabilities, step Skill capabilities, and preset/template capabilities.
- Existing tests already cover advanced-mode inputs that produce top-level capabilities such as `codex_cli`, `git`, `gh`, and `jira`.
- Presets can declare and derive capabilities. Jira presets such as `jira-orchestrate.yaml` and `jira-implement.yaml` already carry `jira` capability metadata.
- The backend execution contract already preserves and derives capabilities from top-level, task Skill, step Skill, and Tool fields.
- The Skill System already has an adjacent transitive metadata concept for `metadata.required-skills`, which proves Skill frontmatter can drive resolution-time behavior.

## Problem statement

`jira-pr-verify` can fail when it is selected directly rather than through a Jira preset. The preset path supplies capability metadata, but the direct Skill path does not automatically add or enforce `jira`, `git`, or `gh` requirements.

That leaves the managed runtime able to launch without trusted Jira content, without a verified Jira tool path, or without usable GitHub PR/comment access. The agent then discovers the missing access too late and may report a task-specific blocker that the platform could have detected before launch.

## Non-goals

- Do not create a parallel "GitHub/Jira access" feature.
- Do not put secrets or raw credentials in Skill files, Skill manifests, or prompts.
- Do not treat Skill-declared capabilities as automatic authorization grants.
- Do not let frontend capability derivation be the only enforcement layer.
- Do not make runtimes rediscover or broaden capabilities after launch.

## Decisions

1. Agent Skills may declare default runtime capabilities in Skill metadata.
2. Direct Skill selection must merge those declared capabilities into the existing `requiredCapabilities` payload, just as presets already contribute capabilities.
3. Backend normalization must be authoritative. The frontend may derive early for preview and submit payload shape, but backend execution contract and runtime preparation must not trust the browser as the only source of capability requirements.
4. `requiredCapabilities` are hard launch requirements. They are not only labels for routing or display.
5. Unsatisfied required capabilities must block before agent launch with actionable diagnostics.
6. Coarse capability names remain supported now (`git`, `gh`, `jira`). Scoped names are allowed as a future refinement (`jira.read`, `github.pr.read`, `github.pr.comment`) but should map to the same enforcement pipeline instead of a second system.

## Skill metadata contract

Repo and built-in Skill Markdown should use frontmatter metadata:

```yaml
---
name: jira-pr-verify
description: Verify a GitHub pull request against a Jira issue's goals, requirements, and acceptance criteria, then post a PR comment with the findings.
metadata:
  required-capabilities:
    - jira
    - git
    - gh
---
```

Rules:

1. `metadata.required-capabilities` is a YAML sequence of strings.
2. Capability tokens are normalized to lowercase and deduplicated while preserving first-seen order.
3. Invalid, blank, or non-string tokens fail Skill resolution before runtime launch.
4. Deployment-stored Skill versions should store the same data in artifact metadata as `required_capabilities: string[]`; the resolver may expose both sources as `ResolvedSkillEntry.required_capabilities`.
5. Capability metadata is safe declarative metadata. It must not contain credentials, tokens, URLs with credentials, or user-specific secrets.
6. Capability metadata is additive. A selected Skill's default requirements may be augmented by presets, advanced authoring fields, publish mode, runtime mode, or Tool selections, but they must not be silently removed by the authoring UI.

## Capability semantics

| Capability | Minimum meaning now | Future scoped aliases |
| --- | --- | --- |
| `git` | Managed runtime has a repository checkout, `git` is installed, and the requested repo/branch state can be prepared. | `repo.read`, `repo.write` |
| `gh` | GitHub CLI or equivalent GitHub runtime path is present and authenticated for the target repository. For PR-commenting Skills, comment permission must be verified before launch. | `github.pr.read`, `github.pr.comment` |
| `jira` | Trusted Jira control-plane/tool access is available, or normalized Jira artifacts are prefetched and materialized for the run. Raw Atlassian credentials are not placed in the managed shell. | `jira.read`, `jira.issue.read` |

The coarse names should continue to work until the platform has a stable scoped vocabulary and compatibility mapping.

## Implementation plan

### Phase 1 — Data model and parsing

- Extend `ResolvedSkillEntry` with `required_capabilities: list[str]`.
- Parse `metadata.required-capabilities` from repo and built-in `SKILL.md` frontmatter alongside `metadata.required-skills`.
- Read deployment artifact metadata `required_capabilities` for deployment-stored Skills.
- Normalize and validate capability tokens in one helper, similar to required-Skill name parsing.
- Include the final per-Skill capability list in the resolved manifest and source trace so detail/debug views can explain why a run required a capability.

### Phase 2 — Direct Skill selection merge

- Expose declared Skill capabilities through the Skill catalog / selector data used by the dashboard.
- When a user selects a Skill directly at the task level, merge that Skill's declared capabilities into the existing task Skill capability list before calling `deriveRequiredCapabilities`.
- When a user selects a Skill directly on a step, merge that Skill's declared capabilities into the existing step Skill capability list.
- Keep Advanced Mode capability entries additive and visible; do not duplicate values when the selected Skill already declares them.
- Preserve preset-derived capabilities exactly as today.

### Phase 3 — Backend authoritative normalization

- In execution contract normalization, merge capabilities from:
  - incoming top-level `requiredCapabilities`,
  - runtime mode,
  - publish mode,
  - selected task Skill declaration,
  - selected step Skill declarations,
  - explicit task/step Skill `requiredCapabilities`,
  - step Tool `requiredCapabilities`,
  - preset/template-derived capabilities.
- Do not rely on live preset lookup for already compiled presets.
- If the frontend omitted a directly selected Skill's default capabilities, backend Skill resolution must still derive them before launch.
- Persist the normalized, deduplicated top-level `requiredCapabilities` list as the execution contract used by runtime preparation.

### Phase 4 — Runtime capability readiness gates

Treat each normalized capability as a launch preflight requirement.

- `git`: verify the checkout exists, `git` is executable, the expected repository is available, and branch state can be prepared for the publish mode.
- `gh`: verify `gh` or the canonical GitHub runtime path is available, authentication is usable, the target repo/PR can be viewed, and PR comment permission is available when the selected Skill can post comments.
- `jira`: verify trusted Jira tool readiness or prefetch Jira artifacts before agent launch. If the Skill requires Jira issue content and no issue artifact exists, call the trusted Jira fetch/tool path in the control plane and materialize sanitized artifacts into the run workspace.
- If any required capability cannot be satisfied, fail before agent launch with a structured blocker that names the capability, target, attempted readiness check, and remediation.

### Phase 5 — Seed metadata and audit existing Skills

- Add `metadata.required-capabilities: [jira, git, gh]` to `jira-pr-verify`.
- Audit built-in Skills for hard dependencies implied by their instructions:
  - PR resolvers and comment fixers likely require `git` and `gh`.
  - Jira triage / Jira implementation / Jira verification Skills likely require `jira` and often `git` or `gh`.
  - Docs-only Skills should avoid declaring external capabilities unless they actually need them.
- Keep capability declarations minimal. A Skill should not declare `jira` merely because a user might mention Jira; it should declare `jira` when the Skill's normal operation needs trusted Jira access or Jira artifacts.

### Phase 6 — Tests

Minimum coverage:

1. Parser accepts `metadata.required-capabilities` YAML arrays and rejects invalid shapes.
2. Resolver exposes Skill-declared capabilities on `ResolvedSkillEntry` and resolved manifests.
3. Selecting `jira-pr-verify` directly produces top-level `requiredCapabilities` containing `codex_cli`, `git`, `gh`, and `jira`, even when no Jira preset is selected.
4. Existing Advanced Mode tests for explicit `git`, `jira` remain green.
5. Preset-derived capability tests remain green and do not double-add duplicates.
6. Backend normalization derives Skill-declared capabilities even if the frontend omits them.
7. Runtime preflight blocks before agent launch when `jira` is required but neither trusted Jira tooling nor prefetched Jira artifacts are available.
8. Runtime preflight blocks before agent launch when `gh` is required but repo/PR read or comment permission is unavailable.
9. Failure diagnostics are structured and do not include secrets or environment dumps.

## Acceptance criteria

- Directly selecting `jira-pr-verify` launches only after Jira and GitHub readiness have been verified or required artifacts have been materialized.
- The submitted execution payload includes normalized top-level capabilities such as `codex_cli`, `git`, `gh`, and `jira` for that direct Skill path.
- A missing Jira or GitHub prerequisite blocks before the agent starts, not after the agent has already begun reasoning.
- Preset-backed Jira workflows continue to work through the same `requiredCapabilities` field.
- The system has one capability contract, not a new per-integration access mechanism.

## Initial exemplar change

`jira-pr-verify` now declares:

```yaml
metadata:
  required-capabilities:
    - jira
    - git
    - gh
```

That declaration matches the Skill's documented behavior: it needs trusted Jira issue content, repository/PR inspection, and authenticated GitHub comment access.
