# Tasks: Claude Runtime API-Key Gating

## [Done]

- [ ] T001 Update quickstart and runtime environment docs in `README.md` to remove legacy Claude-auth bootstrap references and document `ANTHROPIC_API_KEY` gating.
- [ ] T002 Update `docs/TaskUiArchitecture.md` runtime section so Claude is runtime-gated by supportedTaskRuntimes.
- [ ] T003 Remove stale OAuth-oriented spec content and replace with `specs/027-claude-api-key-gate`.
- [ ] T004 Add `tools/check-no-claude-oauth-refs.sh` to detect remaining OAuth-era references.
