---
name: omnigent-3561-publication-semantics
description: State of MoonMind#3561 Omnigent repository publication semantics + durable authority-chain evidence — what this branch added
metadata:
  type: project
---

GitHub issue MoonLadderStudios/MoonMind#3561 ([Omnigent M1 4/8] Preserve repository publication semantics and durable workspace evidence) was assessed PARTIALLY_IMPLEMENTED: the shared substrate from parent [[omnigent-3507-workspace-materialization]] (#3507) exists, but #3561's own deliverable — proof that publication/saved-work/recovery obey canonical contracts through the Omnigent path, plus a unified bounded authority-chain projection — was absent. Finished on branch `github-issue-implement-moonladderstudios-63cbaf04`.

**Architecture note (confirmed):** the Omnigent coordinator (`moonmind/omnigent/profile_bound_execution.py`) does NOT publish; it returns an `AgentRunResult`. Publication flows through the shared `agent_run.py` workflow → `agent_runtime_fetch_result` / `_push_workspace_branch` and `agent_runtime_publish_terminal_checkpoint` (in `activity_runtime.py`). publishMode short forms are `none`/`branch`/`pr`/`auto`. `PublishService.publish` (`moonmind/publish/service.py`) is a self-contained canonical publisher (branch/commit/push/PR + outbound scan). `publication_recovery.py` is provider-agnostic (no Omnigent coupling by design).

**Done in this change:**
- New `moonmind/omnigent/authority_chain.py`: pure, total, secret-scanned `build_omnigent_authority_chain_evidence(...)` assembling the unified workspace→runtime→publication→terminal→cleanup→lease projection (AC5/AC3/AC6). Runs through `redact_sensitive_payload`; never raises.
- Coordinator wiring: emits one `authority_chain` lifecycle event (nested under a single `authorityChain` metadata key) before `terminal`, on both success and failure paths. Added `authorityChain` to the bridge-store metadata allowlist (`bridge_store.py` ~line 1217).
- Controlling journey `tests/integration/reliability_journey/test_omnigent_publication_semantics_journey.py` (integration_ci, hermetic — real git + bare remote + real `PublishService` + real `_prepare_workspace` + real coordinator): branch publish + idempotent publication-only recovery, canonical PR publish, no-publish terminal saved-work checkpoint with distinct source/output branches, and coordinator materialization→cleanup→replay for static (read-only) and on-demand (mutation) modes.
- Unit tests: `tests/unit/omnigent/test_authority_chain_evidence.py` (6), `test_bridge_store.py::test_authority_chain_metadata_persists_through_allowlist`, `test_oauth_profile_lifecycle.py::test_coordinator_emits_bounded_authority_chain_before_terminal`.

**Still open on #3561:** AC8 (update parent #3507 with resolvable evidence before closure) is an external issue-tracking action, not a repo change. A frontend Workflow-Detail renderer specifically for the `lifecycle.authority_chain` event was not added (the event + bounded metadata reach the existing events projection intact; dedicated UI is optional polish).

**Env note:** `tests/unit/omnigent/test_host_protocol_adapter.py`, `test_host_auth_profile.py`, `test_host_auth_remediation.py`, `test_embedded_host_channel.py` fail with "pinned Omnigent ... unavailable" — the same pre-existing missing-upstream environment blocker noted for #3507; confirmed identical with this change stashed, unrelated.
