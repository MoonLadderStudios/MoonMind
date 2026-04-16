# Quickstart: OAuth Terminal Docker Verification

## 1. Confirm Docker Availability

```bash
test -S /var/run/docker.sock
docker ps
```

Expected: Docker socket exists and the daemon responds.

Current managed-agent result for this run:

```text
/var/run/docker.sock is unavailable; docker ps cannot connect to the daemon.
```

## 2. Run Required Integration Verification

```bash
./tools/test_integration.sh
```

Expected in a Docker-enabled environment: the hermetic `integration_ci` suite passes, including OAuthTerminal-relevant managed-session coverage.

If blocked: record the exact Docker or compose prerequisite failure and do not close prior ADDITIONAL_WORK_NEEDED reports.

Current managed-agent result for this run:

```text
./tools/test_integration.sh exited with code 1 after creating .env from .env-template.
The command failed before tests ran because Docker could not connect to unix:///var/run/docker.sock:
dial unix /var/run/docker.sock: connect: no such file or directory.
```

## 3. Diagnose Focused Runtime Boundaries When Needed

Use focused targets only after the required command identifies a specific failing area:

```bash
python -m pytest tests/integration/services/temporal/test_codex_session_task_creation.py -q --tb=short
python -m pytest tests/integration/services/temporal/test_codex_session_runtime.py -q --tb=short
python -m pytest tests/integration/temporal/test_oauth_session.py -q --tb=short
```

## 4. Run Unit Checks If Harness Fixes Are Needed

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py
```

Use this command only to validate non-Docker runtime or harness fixes. It cannot replace Docker-backed closure evidence.

## 5. Update Reports

Update these reports only after evidence review:

- `specs/175-launch-codex-auth-materialization/verification.md`
- `specs/180-codex-volume-targeting/verification.md`
- `specs/183-oauth-terminal-flow/verification.md`

Rules:
- Passing Docker evidence may close the matching gap.
- Missing Docker access keeps ADDITIONAL_WORK_NEEDED with the exact blocker.
- Redact secrets and summarize logs.
