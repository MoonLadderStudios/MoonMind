# MoonSpec Story Breakdown

Source: docs/Omnigent/OmnigentHostMountedTools.md
Class: canonical-declarative
Extracted: 2026-07-17
Output mode: Jira

## Design summary

MoonMind extends unchanged stock omnigent-host containers with one trusted, pinned, immutable, read-only CLI bundle projected into ordinary, login, and runner shells. Required capabilities are proven before session creation; credentials and resolved Skills remain separate authority boundaries. The initial gh outcome supports isolated GitHub workflows, while eligibility and diagnostics rules keep the mechanism bounded.

The canonical source remains authoritative; this is a temporary derived view.

## Coverage points

- DESIGN-REQ-001: unchanged upstream host and bounded mounted-tool scope
- DESIGN-REQ-002: standard manifest-backed bundle layout and deployment-owned identity
- DESIGN-REQ-003: pinned digest-verified probe-validated atomic initialization
- DESIGN-REQ-004: immutable version binding and distinct upgrade publication
- DESIGN-REQ-005: read-only static and on-demand mounts with daemon-visible sources
- DESIGN-REQ-006: ordinary, login, and runner PATH visibility preserving upstream PATH
- DESIGN-REQ-007: required-capability preflight before session creation or mutation
- DESIGN-REQ-008: real gh capability in addition to git
- DESIGN-REQ-009: credential separation, run isolation, and private GH configuration
- DESIGN-REQ-010: authenticated private pre-host clone and in-host operations
- DESIGN-REQ-011: repository-scoped gh authentication and authorization readiness
- DESIGN-REQ-012: resolved Skills remain semantic authority
- DESIGN-REQ-013: future-tool eligibility, shared bundle, and upstream retirement
- DESIGN-REQ-014: bounded redacted diagnostics and run-scoped health
- DESIGN-REQ-015: no agent installation or shared-tool mutation
- DESIGN-REQ-016: excluded package-manager, broker, daemon, privilege, and large-toolchain scope

## Ordered story candidates

### STORY-001: Publish a pinned immutable Omnigent tool bundle

Source: docs/Omnigent/OmnigentHostMountedTools.md — 1. Purpose, 4. Canonical tool bundle, 5. Tool initialization
Claims: OMHMT-purpose-01, OMHMT-bundle-01, OMHMT-bundle-02, OMHMT-init-01
Coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-015
Dependencies: None

Why: As a platform operator, I can publish a complete verified versioned CLI bundle without changing the upstream host image.

Independent test: Run valid, mismatched, interrupted, and repeated initializer fixtures; only a complete matching bundle becomes available and existing hosts keep their version.

Acceptance criteria:
- Use /opt/moonmind-tools/manifest.json and bin/<ordinary-command>.
- Pin platform artifacts, verify SHA-256, permissions, and bounded version probes.
- Publish atomically from attempt-private staging; reject manifest mismatch instead of modifying in place.
- Allow only the trusted idempotent initializer to write; agents cannot install or mutate tools.
- Publish upgrades as distinct completed versions.

### STORY-002: Project tools into static and on-demand runner shells

Source: docs/Omnigent/OmnigentHostMountedTools.md — 6. Host mounts and shell visibility
Claims: OMHMT-mount-01
Coverage: DESIGN-REQ-005, DESIGN-REQ-006
Dependencies: STORY-001

Why: As an operator, I can expose the same read-only bundle to static and on-demand stock hosts and their runners.

Independent test: Launch both host types and verify ordinary processes, bash -lc, and the exact runner construction resolve the tool while upstream commands remain visible.

Acceptance criteria:
- Mount the bundle at /opt/moonmind-tools and the profile snippet read-only.
- Set PATH directly and prepend it idempotently for login shells.
- Preserve the discovered upstream PATH and never cover /usr/local/bin.
- Use versioned named volumes or validated daemon-visible bind sources consistently.

### STORY-003: Preflight required CLI capabilities before sessions

Source: docs/Omnigent/OmnigentHostMountedTools.md — 8. Capability readiness, 10. Failure and observability contract
Claims: OMHMT-ready-01, OMHMT-observe-01
Coverage: DESIGN-REQ-007, DESIGN-REQ-011, DESIGN-REQ-014
Dependencies: STORY-002

Why: As a workflow operator, I receive proof that every declared CLI capability is usable before reasoning or mutation begins.

Independent test: Exercise missing, non-executable, login-hidden, runner-hidden, unauthenticated, unauthorized, and valid tools; only valid required runs create sessions, while optional absence stays scoped.

Acceptance criteria:
- Run command lookup and trusted manifest probes in the actual host and exact runner paths.
- For gh, prove version, authentication, target repository access, and required mutation permissions.
- Block before session creation or mutation with an actionable stable failure class.
- Expose only bounded redacted evidence and do not mark a host unhealthy for an absent optional tool.

### STORY-004: Authorize isolated GitHub-aware Omnigent runs

Source: docs/Omnigent/OmnigentHostMountedTools.md — 7. Initial gh capability
Claims: OMHMT-gh-01
Coverage: DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010
Dependencies: STORY-002

Why: As an operator, I can run GitHub-aware Skills against private repositories with isolated Git and gh authorization and no durable secret exposure.

Independent test: Clone a private repository before host launch and run repository-scoped git and gh commands in a dedicated runner; verify secrets are absent from URLs, payloads, histories, logs, artifacts, metadata, and other sessions.

Acceptance criteria:
- Supply the real gh CLI in addition to git.
- Resolve credentials separately from the tool bundle at each trusted boundary.
- Use on-demand/run-dedicated hosts unless verified per-run injection exists.
- Use owner-only run-private GH_CONFIG_DIR and explicit GH runner passthrough with prompts and update notifiers disabled.
- Keep repository URLs token-free and durable records secret-free.
- Do not inject credentials merely because gh is mounted.

### STORY-005: Compose resolved Skills with mounted executable capabilities

Source: docs/Omnigent/OmnigentHostMountedTools.md — 3. Architectural decision summary, 7.5 Relationship to resolved Skills
Claims: OMHMT-decisions-01, OMHMT-skill-01
Coverage: DESIGN-REQ-012
Dependencies: STORY-003, STORY-004

Why: As a Skill author, my resolved immutable Skill remains semantic authority while the runner supplies its declared real CLI dependencies.

Independent test: Run the resolved pr-resolver closure with active Skill projection and mounted gh; verify portable Skill helpers perform GitHub semantics and no adapter duplicate does so.

Acceptance criteria:
- Materialize the resolved immutable Skill closure separately and read-only.
- Expose both the active Skill directory and mounted tool bin to the runner.
- Provide the real ordinary CLI declared by the Skill.
- Do not move collection, classification, CI interpretation, retry, or merge decisions into the adapter.
- Fail before mutation when either required projection is unavailable.

### STORY-006: Govern mounted-tool eligibility and retirement

Source: docs/Omnigent/OmnigentHostMountedTools.md — 2. Scope, 9. Use for future tools, 11. Conformance requirements
Claims: OMHMT-scope-01, OMHMT-future-01, OMHMT-conform-01
Coverage: DESIGN-REQ-013, DESIGN-REQ-016
Dependencies: STORY-003

Why: As a platform maintainer, I can keep the mounted bundle small by enforcing eligibility and removing tools once upstream safely supplies them.

Independent test: Evaluate eligible and image-level tool examples and simulate upstream replacement; only bounded CLIs enter the shared bundle and redundant copies retire after readiness passes.

Acceptance criteria:
- Admit only demonstrated stable non-interactive prebuilt CLIs compatible with stock libraries and preflight.
- Use the shared bundle rather than per-tool layouts.
- Reject package managers, marketplaces, RPC emulators, brokers, privileged/kernel changes, daemons, entrypoint changes, and large toolchains.
- Prefer upstream host additions for image-level needs; require explicit ownership for a derived image.
- Remove a duplicate mounted executable after upstream compatibility and readiness checks pass.

## Coverage matrix

- OMHMT-purpose-01 -> STORY-001
- OMHMT-scope-01 -> STORY-006
- OMHMT-decisions-01 -> STORY-005
- OMHMT-bundle-01 -> STORY-001
- OMHMT-bundle-02 -> STORY-001
- OMHMT-init-01 -> STORY-001
- OMHMT-mount-01 -> STORY-002
- OMHMT-gh-01 -> STORY-004
- OMHMT-skill-01 -> STORY-005
- OMHMT-ready-01 -> STORY-003
- OMHMT-future-01 -> STORY-006
- OMHMT-observe-01 -> STORY-003
- OMHMT-conform-01 -> STORY-006
- DESIGN-REQ-001 -> STORY-001
- DESIGN-REQ-002 -> STORY-001
- DESIGN-REQ-003 -> STORY-001
- DESIGN-REQ-004 -> STORY-001
- DESIGN-REQ-005 -> STORY-002
- DESIGN-REQ-006 -> STORY-002
- DESIGN-REQ-007 -> STORY-003
- DESIGN-REQ-008 -> STORY-004
- DESIGN-REQ-009 -> STORY-004
- DESIGN-REQ-010 -> STORY-004
- DESIGN-REQ-011 -> STORY-003
- DESIGN-REQ-012 -> STORY-005
- DESIGN-REQ-013 -> STORY-006
- DESIGN-REQ-014 -> STORY-003
- DESIGN-REQ-015 -> STORY-001
- DESIGN-REQ-016 -> STORY-006

## Out of scope

No custom host fork, package manager/marketplace, per-run downloads, CLI-emulating RPC service, credential broker, Skill storage redesign, privileged/kernel changes, daemons, or broad system installation.

## Coverage gate

PASS - every major design point is owned by at least one story.

## Downstream guidance

Recommended first story: STORY-001. No stories need clarification. TDD remains the default for downstream plan, tasks, and implementation; run /moonspec.verify afterward against the original design preserved through specify.
