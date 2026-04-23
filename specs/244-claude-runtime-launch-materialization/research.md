# Research: Claude OAuth Runtime Launch Materialization

## FR-001 / DESIGN-REQ-004

Decision: implemented_unverified until Claude OAuth-home launch/session tests prove the selected `claude_anthropic` profile is resolved and carried through launch.
Evidence: `api_service/main.py` auto-seeds `claude_anthropic`; `moonmind/workflows/adapters/managed_agent_adapter.py` copies `MOONMIND_EXECUTION_PROFILE_REF` and `MANAGED_AUTH_VOLUME_PATH`; `tests/unit/workflows/temporal/test_agent_runtime_activities.py` proves safe auth diagnostics for Codex, not Claude.
Rationale: The launch path already has the right plumbing, but the current evidence is runtime-generic or Codex-specific rather than MM-481-specific.
Alternatives considered: Treat generic runtime/profile handling as sufficient. Rejected because MM-481 requires Claude-specific launch proof before implementation can be considered complete.
Test implications: Unit tests for Claude managed-session launch diagnostics and launcher profile resolution.

## FR-002 / DESIGN-REQ-003

Decision: partial because the seeded Claude OAuth profile shape exists, but the launch boundary has not been proven to preserve it unchanged.
Evidence: `api_service/main.py` seeds `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `volume_ref = claude_auth_volume`, `volume_mount_path = /home/app/.claude`, and `clear_env_keys` including `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY`; `moonmind/schemas/agent_runtime_models.py` validates required volume fields for `oauth_home` profiles.
Rationale: The source-of-truth profile shape exists and is validated, but MM-481 is about launch-time consumption of that shape, not just seeding.
Alternatives considered: Mark implemented_verified from seeded profile and schema validation alone. Rejected because launch could still drop, override, or ignore the profile shape.
Test implications: Unit tests must prove launch/session shaping consumes the seeded OAuth-home fields intact.

## FR-003 / DESIGN-REQ-015

Decision: partial because provider defaults and auth-mount metadata exist, but there is no Claude OAuth launch proof for auth-volume materialization at `/home/app/.claude`.
Evidence: `moonmind/workflows/temporal/runtime/providers/registry.py` defines Claude defaults; `moonmind/workflows/temporal/activity_runtime.py` includes `volumeRef` and `authMountTarget` in safe diagnostics; existing tests prove this path only for Codex (`tests/unit/workflows/temporal/test_agent_runtime_activities.py`).
Rationale: The managed-session activity already reports the mount target safely, which is the right boundary to extend for Claude. The remaining risk is absent Claude-specific verification.
Alternatives considered: Add only launcher tests. Rejected because session-launch diagnostics are the operator-visible boundary that proves mount targeting without leaking secrets.
Test implications: Unit tests for both launcher materialization and managed-session diagnostics with `claude_code` + OAuth-home profile.

## FR-004

Decision: partial because the codebase contains the expected Claude home env behavior, but not yet verified on the MM-481 launch path.
Evidence: `moonmind/agents/base/adapter.py` injects `CLAUDE_HOME` for `claude_code`; `moonmind/agents/codex_worker/runtime_mode.py` and `moonmind/agents/codex_worker/cli.py` enforce OAuth `CLAUDE_HOME` presence and writability; `moonmind/workflows/temporal/runtime/terminal_bridge.py` sets `CLAUDE_HOME` and `CLAUDE_VOLUME_PATH` for the Claude OAuth terminal flow.
Rationale: The code suggests the intended behavior already exists across related boundaries, but the runtime launch path needs direct proof that it sets Claude home variables consistently for task execution.
Alternatives considered: Treat terminal-bridge coverage as sufficient. Rejected because MM-481 is the post-verification task-launch path, not the OAuth terminal flow.
Test implications: Unit tests should assert `CLAUDE_HOME` and `CLAUDE_VOLUME_PATH` on the actual Claude launch/session path.

## FR-005 / SC-002 / DESIGN-REQ-018

Decision: partial because generic environment clearing exists, but Claude OAuth-home launch lacks boundary proof that all competing keys are absent.
Evidence: `moonmind/workflows/adapters/materializer.py` removes `profile.clear_env_keys`; the seeded Claude OAuth profile includes `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY`; current launcher tests prove key clearing generically and for Claude API-key secret-ref launch, not Claude OAuth-home launch.
Rationale: The implementation path is likely correct, but the missing MM-481 evidence is exactly the kind of regression the story is meant to prevent.
Alternatives considered: Rely on generic clear-env tests. Rejected because the Claude story names three specific competing keys and an OAuth-backed launch path.
Test implications: Focused launcher or session tests that seed ambient values and prove all three are absent before Claude runtime start.

## FR-006 / SC-004 / DESIGN-REQ-017

Decision: implemented_unverified because sanitized auth diagnostics already exist, but Claude launch-specific no-leak evidence is missing.
Evidence: `moonmind/workflows/temporal/activity_runtime.py` sanitizes managed-session errors; `tests/unit/workflows/temporal/test_agent_runtime_activities.py` verifies redaction for Codex auth diagnostics; `docs/ManagedAgents/ClaudeAnthropicOAuth.md` requires Claude launch secrecy.
Rationale: Sanitization infrastructure exists and is likely reusable. The missing work is to extend or verify it for Claude launch surfaces and auth-volume paths.
Alternatives considered: Mark partial and pre-plan a broader artifact audit. Rejected because the current code already shows the right sanitization pattern; first step is Claude-specific proof.
Test implications: Unit tests for Claude auth-diagnostics metadata and redacted error summaries, with an integration contingency only if artifact publication behavior changes.

## FR-007

Decision: missing because no current test proves the Claude auth volume is excluded from task workspace or artifact-backed paths during launch and audit flows.
Evidence: `moonmind/workflows/temporal/runtime/launcher.py` treats the repo workspace and support directory separately, but no Claude-specific assertion ties auth volume handling to workspace/artifact exclusion.
Rationale: This is the least-proven MM-481 requirement and likely where production code may still need a small boundary fix or explicit metadata guard.
Alternatives considered: Assume `MANAGED_AUTH_VOLUME_PATH` separation is enough. Rejected because the spec requires a concrete guarantee that the auth volume is not treated as workspace or artifact storage.
Test implications: Unit tests first; add hermetic integration coverage if launcher/artifact behavior changes materially.

## FR-008

Decision: implemented_verified.
Evidence: `specs/244-claude-runtime-launch-materialization/spec.md` preserves MM-481 and the full original preset brief.
Rationale: Traceability is already satisfied at the planning boundary and must be preserved downstream.
Alternatives considered: None.
Test implications: None beyond final verify.

## Test Strategy

Decision: Use focused unit tests as the primary TDD harness, with integration tests only if launch/artifact behavior changes beyond current in-process boundaries.
Evidence: Existing repo guidance requires `./tools/test_unit.sh` for final unit verification and reserves `./tools/test_integration.sh` for hermetic compose-backed seams. Current MM-481 gaps are concentrated in launcher, session activity, and CLI preflight boundaries already covered by unit suites.
Rationale: The missing coverage is boundary behavior inside Python services, so unit tests provide fast red/green cycles. Integration should remain contingency-based to avoid unnecessary compose cost when no external seam changes.
Alternatives considered: Start with integration tests. Rejected because the story’s first gap is missing direct unit evidence and integration would be slower to diagnose.
Test implications: Primary commands are focused `./tools/test_unit.sh` targets, followed by full `./tools/test_unit.sh`; run `./tools/test_integration.sh` only when launch-to-artifact or worker-topology behavior changes.
