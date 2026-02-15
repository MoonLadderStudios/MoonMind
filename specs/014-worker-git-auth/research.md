# Research: Worker GitHub Token Authentication Fast Path

## Decision 1: Run GitHub auth preflight in worker CLI startup

- **Decision**: Extend `run_preflight()` in `moonmind/agents/codex_worker/cli.py` to verify `gh` availability and execute GitHub auth setup checks before the poll loop starts.
- **Rationale**: Startup is the safest place to fail fast and prevents partially-authenticated workers from claiming jobs they cannot clone/publish.
- **Alternatives considered**:
  - Lazy auth during first clone: rejected because it delays failure and complicates operator diagnostics.
  - Handler-level auth setup on every job: rejected because it adds repeated overhead and duplicates startup responsibilities.

## Decision 2: Keep token material out of command arguments and logs

- **Decision**: Use `subprocess.run(..., input=<token>)` with `gh auth login --with-token` so token material is passed via stdin and never appears in argv; sanitize surfaced stderr/stdout text for any raw token echoes.
- **Rationale**: Meets the document guardrails requiring command logs while preventing raw token exposure.
- **Alternatives considered**:
  - Tokenized clone URLs (`https://token@github.com/...`): rejected because explicitly forbidden and leak-prone.
  - Temporarily writing token to disk: rejected due avoidable secret persistence risk.

## Decision 3: Explicitly reject tokenized repository URLs

- **Decision**: Validate HTTPS repository inputs and reject any URL containing userinfo credentials (for example, `https://<token>@github.com/...`) before clone execution.
- **Rationale**: Prevents accidental secret leakage in command logs, process metadata, and downstream artifacts.
- **Alternatives considered**:
  - Allow credentials and rely on log redaction: rejected because prevention is stronger than best-effort masking.
  - Only warn without rejecting: rejected because contract states these values must never be sent.

## Decision 4: Preserve existing publish and queue behavior

- **Decision**: Keep current queue payload schema, handler flow (`clone` -> `codex exec` -> `diff` -> optional publish), and worker claim policy semantics unchanged while inserting auth/safety checks.
- **Rationale**: Fast path contract requires minimal change footprint and no queue schema/repoAuthRef rollout.
- **Alternatives considered**:
  - Introduce new payload auth fields now: rejected as out-of-scope for fast path.
  - Rework publish flow around new credential helper architecture: rejected as larger than required now.
