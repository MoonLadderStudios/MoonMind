# Requirements Traceability: Cursor CLI Phase 1

**Feature**: 088-cursor-cli-phase1
**Source Contract**: `docs/ManagedAgents/CursorCli.md`

| DOC-REQ | FR Mapping | Implementation Surface | Validation Strategy |
|---------|-----------|----------------------|---------------------|
| DOC-REQ-001 | FR-001 | `api_service/Dockerfile` — add `curl https://cursor.com/install` + rename binary | Build Docker image, run `cursor-agent --version` inside container |
| DOC-REQ-002 | FR-002 | `tools/auth-cursor-volume.sh` — new script with `--api-key`, `--login`, `--check`, `--register` modes | Run each mode and verify exit codes + expected output |
| DOC-REQ-003 | FR-003, FR-004 | Container runtime verification after Dockerfile + auth setup | Run `cursor-agent status` and `cursor-agent -p` with valid credentials in container |
| DOC-REQ-004 | FR-005 | `.env-template` — add `CURSOR_API_KEY` entry with documentation | Grep `.env-template` for `CURSOR_API_KEY` |
| DOC-REQ-005 | FR-006 | `research.md` R2 decision + Dockerfile pin strategy | Document auto-update disabled-by-default in immutable Docker layers |
| DOC-REQ-006 | FR-007 | `tools/auth-cursor-volume.sh --api-key` mode | Verify API key auth flow works end-to-end |
| DOC-REQ-007 | FR-008 | `docker-compose.yaml` — add `cursor_auth_volume`, `cursor-auth-init` service, worker mounts | Run `docker compose config` and verify volume/service definitions |
