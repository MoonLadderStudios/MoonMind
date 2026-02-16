# Requirements Traceability: Worker GitHub Token Authentication Fast Path

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | WorkerGitAuth.md:9-13,33 | FR-001 | `moonmind/agents/codex_worker/cli.py` | CLI unit tests verify token-present startup triggers GitHub auth setup path. |
| DOC-REQ-002 | WorkerGitAuth.md:17-20 | FR-002 | `moonmind/agents/codex_worker/cli.py`, `moonmind/agents/codex_worker/handlers.py` | Regression tests confirm no queue payload/schema changes and existing handler inputs still work. |
| DOC-REQ-003 | WorkerGitAuth.md:47,61 | FR-001 | `moonmind/agents/codex_worker/cli.py` | CLI unit tests verify missing `gh` or auth setup failure exits fast with actionable error. |
| DOC-REQ-004 | WorkerGitAuth.md:48-53 | FR-001 | `moonmind/agents/codex_worker/cli.py` | CLI unit tests assert `gh auth login --with-token` and `gh auth setup-git` invocation order when token exists. |
| DOC-REQ-005 | WorkerGitAuth.md:55-59 | FR-001 | `moonmind/agents/codex_worker/cli.py` | CLI unit tests verify `gh auth status` is required and startup fails on non-zero status. |
| DOC-REQ-006 | WorkerGitAuth.md:67-71,75 | FR-003 | `moonmind/agents/codex_worker/handlers.py` | Handler unit tests verify accepted slug/HTTPS/SSH forms and rejection of tokenized HTTPS URLs. |
| DOC-REQ-007 | WorkerGitAuth.md:24-27,63 | FR-002 | `moonmind/agents/codex_worker/handlers.py` | Handler unit tests validate clone/publish command flow remains unchanged for valid repository forms. |
| DOC-REQ-008 | WorkerGitAuth.md:79-82 | FR-003 | `moonmind/agents/codex_worker/cli.py`, `moonmind/agents/codex_worker/handlers.py` | Unit tests verify command logs and surfaced errors are redacted and do not contain token strings. |
| DOC-REQ-009 | WorkerGitAuth.md:107-109 | FR-004 | `moonmind/agents/codex_worker/worker.py`, `tests/unit/agents/codex_worker/test_worker.py` | Worker tests verify claim requests only forward configured job-type/capability hints (policy enforcement remains server-side). |
| DOC-REQ-010 | WorkerGitAuth.md:115-118 | FR-005 | `tests/unit/agents/codex_worker/test_cli.py`, `tests/unit/agents/codex_worker/test_handlers.py`, `tests/unit/agents/codex_worker/test_worker.py` | Unit tests cover startup auth success/failure, tokenized-URL rejection, redaction behavior, claim-policy regression, and full-suite execution via `./tools/test_unit.sh`. |
