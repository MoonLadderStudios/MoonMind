# Tasks: Codex Session Skill Projection Stability

- [x] T001 Add a Codex session adapter regression test proving cold-session launch happens before real turn preparation.
- [x] T002 Add launch metadata regression coverage for preparation-produced durable retrieval metadata.
- [x] T003 Add a pre-launch metadata preparation path that skips selected-skill materialization.
- [x] T004 Reorder Codex managed-session start so real turn instruction preparation runs after `_ensure_remote_session`.
- [x] T005 Add publish filter coverage for `.gemini/skills` and root `skills_active` symlink projections.
- [x] T006 Extend publish filtering to exclude generated compatibility skill symlinks while preserving real checked-in skill directories.
- [x] T007 Run focused unit tests for the affected adapter and activity files.
- [x] T008 Run the full unit suite before opening the PR.
