# Worker Pause Checklist

- [X] Pause reason is mandatory for both pause and resume actions so audit entries remain complete (docs §7.1, spec FR-002/007).
- [X] Claim guard must return `{job:null}` and skip `_requeue_expired_jobs` whenever `workersPaused=true` to avoid mutating queue state (§9.1–9.2).
- [X] Quiesce mode only targets short maintenance windows; running jobs must keep heartbeating and pause at checkpoints rather than releasing leases (§4.1, §8.2).
- [X] Dashboard banner/control needs queued, running, stale counts with an `isDrained` indicator before resume (§10).
- [X] GET `/api/system/worker-pause` must include audit history plus metrics to meet DOC-REQ-006/010 (§7.1, §9.2).

## Manual Quickstart Verification (2026-02-20)

- [X] Captured quickstart rehearsal evidence in `specs/035-worker-pause/manual_verification.log`, mapping each Pause → Drain → Upgrade → Resume plus Quiesce step to the exercised unit suites from `./tools/test_unit.sh`.
- [X] Noted runtime constraint: sqlite+aiosqlite connections hang inside the Codex adapter, so the manual replay uses the executed API/service/worker/dashboard tests as the authoritative verification surface.
