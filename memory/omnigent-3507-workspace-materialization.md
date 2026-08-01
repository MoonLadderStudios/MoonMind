---
name: omnigent-3507-workspace-materialization
description: State of MoonMind#3507 Omnigent normal-workflow workspace materialization — what this branch implemented and what remains
metadata:
  type: project
---

GitHub issue MoonLadderStudios/MoonMind#3507 ([Omnigent P0] normal-workflow workspace materialization + shared host lifecycle) was assessed PARTIALLY_IMPLEMENTED. On branch `github-issue-implement-moonladderstudios-6b2f6b92` I finished the workspace-materialization core (commit references the issue).

**Done in this change** (all in `moonmind/omnigent/oauth_host_runtime.py` `_prepare_workspace` + new `_materialize_repository`/`_materialize_restore_inputs`, wired from `moonmind/omnigent/profile_bound_execution.py`):
- All three `WorkspaceLocator` kinds routed through the one owning-worker boundary; managed/external-state fail closed with `WORKSPACE_LOCATOR_UNSUPPORTED` (AC2/AC4).
- Sandbox path now git-clones + checks out authored starting/target branch (and optional commit) into the single sandbox workspace via the shared `run_runtime_command`; idempotent on retry, preserves in-flight tree (AC1/AC5/AC7/AC9).
- Restore inputs materialized as `artifact://` refs, never conflated with local paths (AC2).
- GitHub token injected only via in-memory git credential helper when source is GitHub HTTPS.
- Bounded credential-free `workspaceResolution` evidence surfaced in preflight + recorded as a `workspace_resolution` lifecycle event (AC6).
- Tests: 11 unit tests in `tests/unit/omnigent/test_oauth_profile_lifecycle.py` + controlling hermetic journey `tests/integration/reliability_journey/test_omnigent_workspace_materialization_journey.py` (AC10, no Docker/network).

**Still open on #3507** (not attempted here — larger/cross-cutting): converging the raw `docker`/`docker compose` lifecycle in `oauth_host_runtime.py` onto shared runtime primitives (AC6 convergence); full output/resource-manifest + partial-start reconciliation; end-to-end publication/PR/terminal-checkpoint semantics through Omnigent (AC9 publish side); janitor-evidence-on-failure across the full lifecycle (AC8). The credentialed browser-to-host acceptance matrix is separately tracked by #3508 (out of scope per this issue's Non-goals).

**Env note:** `tests/unit/omnigent/test_host_protocol_adapter.py`, `test_host_auth_remediation.py`, `test_host_auth_profile.py` fail in this workspace with "pinned Omnigent ... unavailable" — a pre-existing missing-upstream environment blocker, confirmed unrelated to this change (fails identically with the change stashed).
