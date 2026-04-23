# Quickstart: Launch Claude Secret Ref

## Focused Test-First Verification

1. Add a materializer test for a `claude_anthropic` profile using:
   - `secret_refs={"anthropic_api_key": "db://claude_anthropic_token"}`
   - `env_template={"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}}`
   - `clear_env_keys=["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY"]`
2. Confirm the new test fails before any implementation change if the exact profile shape is unsupported.
3. Add a launcher boundary test that monkeypatches secret resolution to return a fake Anthropic token and captures the child process environment.
4. Add a missing-secret launcher test that asserts no child process starts and no raw token appears in error output.

## Commands

Focused unit tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_materializer.py tests/unit/services/temporal/runtime/test_launcher.py
```

Full unit suite before final verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## End-to-End Story Check

The story is satisfied when the focused launcher test proves that a selected `claude_anthropic` profile resolves `anthropic_api_key`, clears conflicting Anthropic/OpenAI keys, injects only `ANTHROPIC_API_KEY`, and fails before process start with secret-free output when the binding is unavailable.
