---
name: launcher-test-broker-env-failures
description: Pre-existing env failure in launcher unit tests inside managed-agent containers (/tmp/mm-gh root-owned)
metadata:
  type: reference
---

In MoonMind-managed agent workspaces, the launcher unit tests in
`tests/unit/services/temporal/runtime/test_launcher.py` that exercise the
GitHub auth broker path (any `launch()` test where `resolve_github_token_for_launch`
returns a token) fail with `PermissionError: Operation not permitted: '/tmp/mm-gh'`.

**Why:** `github_auth_broker.start()` does `Path('/tmp/mm-gh').chmod(0o711)`, and the
socket root `/tmp/mm-gh` is hardcoded (`_build_github_socket_path`, not env-overridable
since `/tmp` always exists). The dir is left root-owned in these containers while tests
run as uid 1000 (`app`), so the chmod is denied. ~17 tests fail this way.

**How to apply:** These failures are environmental and pre-existing (reproduce identically
on `main`). When verifying launcher changes, run only the relevant focused tests
(e.g. `-k generic`) or treat the broker-path failures as noise — don't attribute them to
the change under test. Tests that stub `resolve_github_token_for_launch` to return `None`
skip the broker and run clean.
