# Requirements Traceability: Step Review Gate

| DOC-REQ | FR ID(s) | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR-001 | `tool_plan_contracts.py` — new `ReviewGatePolicy` dataclass | Unit test: construct with valid/invalid args |
| DOC-REQ-002 | FR-002 | `tool_plan_contracts.py` — extend `PlanPolicy` | Unit test: `PlanPolicy` with/without `review_gate` |
| DOC-REQ-003 | FR-003 | `tool_plan_contracts.py` — update `parse_plan_definition()` | Unit test: parse JSON with/without `review_gate` block |
| DOC-REQ-004 | FR-004 | `review_gate.py` — `ReviewRequest` dataclass | Unit test: construction and `to_payload()` |
| DOC-REQ-005 | FR-004 | `review_gate.py` — `ReviewVerdict` dataclass | Unit test: construction, validation of verdict values |
| DOC-REQ-006 | FR-005 | `run.py` — review-retry loop in `_run_execution_stage()` | Unit test: mock node execution + review, verify loop |
| DOC-REQ-007 | FR-006 | `run.py` — retry on `FAIL` | Unit test: mock FAIL→PASS, verify retry count |
| DOC-REQ-008 | FR-006 | `run.py` — INCONCLUSIVE treated as PASS | Unit test: mock INCONCLUSIVE, verify no retry |
| DOC-REQ-009 | FR-007 | `review_gate.py` — `build_feedback_input()` for skill steps | Unit test: verify `_review_feedback` key in inputs |
| DOC-REQ-010 | FR-007 | `review_gate.py` — `build_feedback_instruction()` for agent_runtime | Unit test: verify feedback appended to instruction |
| DOC-REQ-011 | FR-008 | `step_review.py` + `activity_catalog.py` — register route | Unit test: verify activity registered in catalog |
| DOC-REQ-012 | FR-009 | `step_review.py` — prompt construction + LLM call + parsing | Unit test: mock LLM, verify prompt format and parsing |
| DOC-REQ-013 | FR-010 | `run.py` — configuration precedence logic | Unit test: test plan > workflow > env > default |
| DOC-REQ-014 | FR-010 | `run.py` — env var `MOONMIND_REVIEW_GATE_DEFAULT_ENABLED` | Unit test: mock env var, verify behavior |
| DOC-REQ-015 | FR-011 | `run.py` — memo updates during review cycle | Unit test: verify memo string format |
| DOC-REQ-016 | FR-011 | `run.py` — finish summary `reviewGate` metrics | Unit test: verify summary JSON shape |
| DOC-REQ-017 | FR-012 | `run.py` — compose with FAIL_FAST/CONTINUE | Unit test: test both failure modes with review gate |
| DOC-REQ-018 | FR-005 | `run.py` — `skip_tool_types` check | Unit test: node with skipped type bypasses review |
| DOC-REQ-019 | FR-013 | `run.py` — loop determinism | Architectural review: all LLM calls in Activity; loop bounds from frozen policy |
| DOC-REQ-020 | FR-014 | N/A — sizing constraint | Analysis: worst case calculation documented in research.md |
