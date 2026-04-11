# Quickstart: codex-managed-session-phase0-1

1. Run the focused Phase 0 and Phase 1 verification:

```bash
./.venv/bin/pytest -q \
  tests/unit/workflows/temporal/workflows/test_agent_session.py \
  tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py \
  tests/unit/workflows/adapters/test_codex_session_adapter.py \
  tests/unit/workflows/temporal/test_temporal_service.py \
  tests/unit/schemas/test_managed_session_models.py \
  tests/unit/api/routers/test_task_runs.py
```

2. Run the full repo unit suite:

```bash
./tools/test_unit.sh
```

3. Inspect the canonical doc and the workflow surface:

- `docs/ManagedAgents/CodexManagedSessionPlane.md`
- `moonmind/workflows/temporal/workflows/agent_session.py`

Expected outcome: the doc distinguishes truth surfaces explicitly, and workflow mutations go through typed updates with validators rather than the generic `control_action` signal.

4. Verify source requirement traceability:

```bash
python - <<'PY'
from pathlib import Path
import re
text = Path("specs/141-codex-managed-session-phase0-1/spec.md").read_text()
source_ids = sorted(set(re.findall(r"^- \*\*(DOC-REQ-\d+)\*\*:", text, re.M)))
fr_section = text.split("### Functional Requirements", 1)[1]
missing = [doc_id for doc_id in source_ids if not re.search(r"Maps: [^\n]*" + re.escape(doc_id), fr_section)]
raise SystemExit(1 if missing else 0)
PY
```

Expected outcome: every `DOC-REQ-*` maps to at least one functional requirement.
