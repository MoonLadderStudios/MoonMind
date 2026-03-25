# Research: Step Approval Policy

## R1: Temporal Review Patterns

**Decision**: Use a standard Activity for the LLM review inside the existing execution loop.
**Rationale**: Review is nondeterministic (LLM call) and must live in an Activity. The loop is already in `_run_execution_stage()`. Adding a second Activity call per node (the review) is the simplest extension.
**Alternatives considered**: Child workflow for review → rejected (unnecessary overhead, complicates cancellation). Temporal interceptor → rejected (cross-cutting but too opaque for operators).

## R2: Feedback Injection Pattern

**Decision**: Inject feedback as `_review_feedback` key in skill inputs; append to instruction string for agent_runtime.
**Rationale**: Skill executors already accept arbitrary input keys. Agent runtimes read instruction text. Underscore prefix signals system-generated metadata.
**Alternatives considered**: Separate feedback Activity input parameter → rejected (requires changing every skill executor). Out-of-band artifact → rejected (adds complexity; feedback is small JSON).

## R3: Configuration Precedence

**Decision**: Plan-level > workflow-level `initialParameters` > env var `MOONMIND_REVIEW_GATE_DEFAULT_ENABLED` > default off.
**Rationale**: Most specific wins. Plan author's explicit choice overrides run-time defaults.
**Alternatives considered**: Flat env-var-only toggle → rejected (lacks per-plan granularity).

## R4: History Size Impact

**Decision**: Acceptable. Worst case for 20 steps × 2 review retries = 40 extra activities ≈ 120 events. Far under 50K limit.
**Rationale**: Typical Temporal best-practice threshold is ~50K events per execution. Review gate adds at most `N × max_review_attempts` activities.
**Alternatives considered**: Continue-As-New per step → rejected (overkill; review activity events are small).

## R5: Verdict Semantics

**Decision**: `PASS` → proceed, `FAIL` → retry with feedback, `INCONCLUSIVE` → treat as `PASS`.
**Rationale**: Conservative default — don't block workflows on reviewer uncertainty. Operators can evolve toward stricter handling later.
**Alternatives considered**: `INCONCLUSIVE` → retry → rejected (increases cost without clear value).
