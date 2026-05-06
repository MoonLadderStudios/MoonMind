# Tasks: Codex Session Skill Projection Stability

- [x] T001 Add a Codex session adapter regression test proving cold-session launch happens before turn preparation.
- [x] T002 Reorder Codex managed-session start so turn instruction preparation runs after `_ensure_remote_session`.
- [x] T003 Add publish filter coverage for `.gemini/skills` and root `skills_active` symlink projections.
- [x] T004 Extend publish filtering to exclude generated compatibility skill symlinks while preserving real checked-in skill directories.
- [x] T005 Run focused unit tests for the affected adapter and activity files.
- [x] T006 Run the full unit suite before opening the PR.
