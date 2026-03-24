# Quickstart: TmateSessionManager

**Feature**: 104-tmate-session-manager

## Verification Steps

### 1. Run unit tests

```bash
./tools/test_unit.sh
```

All existing tests must pass plus the new `test_tmate_session.py` tests.

### 2. Verify module creation

```bash
python -c "from moonmind.workflows.temporal.runtime.tmate_session import TmateSessionManager, TmateEndpoints, TmateServerConfig; print('OK')"
```

### 3. Verify launcher refactor

```bash
# Confirm no remaining inline tmate logic in launcher.py
grep -c 'tmate_ssh_ro\|tmate_web_ro\|tmate-ready' moonmind/workflows/temporal/runtime/launcher.py
# Expected: 0 (all moved to TmateSessionManager)
```

### 4. Verify self-hosted config generation

```bash
MOONMIND_TMATE_SERVER_HOST=tmate.example.com python -c "
from moonmind.workflows.temporal.runtime.tmate_session import TmateServerConfig
cfg = TmateServerConfig.from_env()
print(f'Host: {cfg.host}, Port: {cfg.port}')
"
# Expected: Host: tmate.example.com, Port: 22
```
