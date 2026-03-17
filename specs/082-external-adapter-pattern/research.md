# Research: Generic External Agent Adapter Pattern

## Decision 1: Scope of Base Class Changes

**Decision**: Minimal incremental changes to `BaseExternalAgentAdapter`.

**Rationale**: The base class already implements DOC-REQ-001 through DOC-REQ-005, DOC-REQ-008. Only two gaps remain: (1) capability-aware `poll_hint_seconds` in `build_handle`, and (2) capability-aware cancel fallback. These are additive changes that don't break the existing contract.

**Alternatives Considered**:
- Full refactor: Rejected — the existing structure already matches the target shape well.
- Abstract cancel entirely: Rejected — providers should still override cancel; the fallback is only for `supportsCancel=False` cases.

## Decision 2: Codex Cloud Activity Pattern

**Decision**: Follow the exact same 4-activity pattern as `jules_activities.py`.

**Rationale**: The `MoonMind.AgentRun` workflow already uses dynamic activity names (`integration.{agent_id}.start/status/fetch_result/cancel`). Adding Codex Cloud activities with the same naming convention requires zero workflow changes — proving DOC-REQ-011.

**Alternatives Considered**:
- Generic activity factory: Rejected — each activity needs its own `_build_adapter()` with provider-specific gating and client construction. A generic factory would sacrifice clarity for minimal code savings.

## Decision 3: `build_handle` Poll Hint Population

**Decision**: Make `build_handle` an instance method (not static) so it can access `self.provider_capability.default_poll_hint_seconds`.

**Rationale**: Currently `build_handle` is a `@staticmethod`. To auto-populate `poll_hint_seconds`, it needs access to the capability descriptor. The cleanest approach is to keep the static helper for backward compatibility and add non-static wrapper logic in `start()` that populates `poll_hint_seconds` on the returned handle if not already set.

**Alternatives Considered**:
- Pass `poll_hint_seconds` explicitly in every `do_start`: Rejected — defeats the purpose of the base class handling it automatically.
- Convert `build_handle` to instance method: Could work but would require updating both provider adapters that call it directly. Instead, add post-processing in `start()`.
