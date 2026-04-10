# Tasks: pr-resolver-retry-policy

## T001 — Add regression coverage for actionable transition handling [P]
- [X] T001a [P] Extend `tests/unit/test_pr_resolver_tools.py` to cover `ci_running -> merge_conflicts -> merged` and assert conflict remediation is invoked instead of `manual_review`.

**Independent Test**: `./tools/test_unit.sh tests/unit/test_pr_resolver_tools.py`

## T002 — Fix pr-resolver transition policy [P]
- [X] T002a [P] Update `.agents/skills/pr-resolver/bin/pr_resolve_orchestrate.py` so actionable remediation reasons reached after finalize-only retry states still escalate through the specialized skill path.

**Independent Test**: `./tools/test_unit.sh tests/unit/test_pr_resolver_tools.py`

## T003 — Verify and finalize [P]
- [X] T003a [P] Run `./tools/test_unit.sh tests/unit/test_pr_resolver_tools.py`.
- [X] T003b [P] Mark completed tasks in this file.

**Independent Test**: Focused pr-resolver tests pass.
