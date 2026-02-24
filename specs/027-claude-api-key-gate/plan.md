# Plan: Claude Runtime API-Key Gating

## Inputs

- `specs/027-claude-api-key-gate/spec.md`

## Scope

This spec set documents the remaining operator-facing cleanup for Claude runtime behavior after API-key gating is enforced in runtime services:

- Documentation updates across operator docs and dashboard architecture docs.
- Replacement of legacy OAuth-oriented specification artifacts.
- A lightweight developer guardrail to detect residual Claude-auth references.

## Work Items

1. Update `README.md` quickstart/runtime guidance.
2. Update `docs/TaskUiArchitecture.md` runtime wording to describe server-driven runtime support.
3. Replace the prior dual-auth OAuth-oriented spec set with this non-OAuth spec set.
4. Add a small helper check in `tools/` to scan docs/spec content for OAuth-era Claude references.
