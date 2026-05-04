# Quickstart: GitHub Token Permission Improvements

## Focused Unit Verification

Run resolver and GitHub service tests first:

```bash
pytest tests/unit/workflows/adapters/test_github_service.py -q
pytest tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py -q
pytest tests/unit/indexers/test_github_indexer.py -q
```

Run publish behavior tests:

```bash
pytest tests/unit/agents/codex_worker/test_handlers.py -q
pytest tests/unit/services/temporal/test_fetch_result_push.py -q
```

Before finalizing implementation, run the full unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Verification

Add or update integration tests that use mocked GitHub HTTP responses and no external credentials. Then run:

```bash
./tools/test_integration.sh
```

Integration coverage should prove:
- Publish mode branch/PR paths invoke explicit credential materialization without ambient `git` or `gh` auth.
- PR readiness reports optional evidence unavailable when checks or issue-reaction permissions are missing.
- Token probe output targets one selected repository and returns mode-specific permission checklist results.

## Test-First Scenarios

1. Resolver precedence:
   - Set multiple token sources.
   - Verify explicit token wins first.
   - Verify `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, secret refs, and `MOONMIND_GITHUB_TOKEN_REF` resolve in the documented order.
   - Verify the result reports only source category, not token value.

2. Publish without ambient auth:
   - Remove ambient `gh` auth assumptions from the test environment.
   - Configure a fake resolved token.
   - Verify branch push receives non-interactive token-aware auth.
   - Verify PR creation uses REST service or token-injected CLI environment.
   - Verify logs and errors redact the token.

3. Permission diagnostics:
   - Mock GitHub 403 with `message`, `documentation_url`, and `X-Accepted-GitHub-Permissions`.
   - Verify user-facing diagnostics include those fields in sanitized form.

4. Optional readiness evidence:
   - Mock checks or issue reactions returning 403 for missing permission.
   - Verify the readiness result names `Checks: read` or `Issues: read` and continues evaluating other allowed evidence.

5. Targeted token probe:
   - Probe `owner/repo`.
   - Verify requests target that repo only.
   - Verify the publish checklist includes contents write and pull requests write, with workflow/checks/issues permissions marked as conditional or optional where applicable.

6. Operator guidance:
   - Review canonical docs for indexing, publishing, readiness, workflow-file modification, and unsupported fine-grained PAT cases.
   - Confirm rollout details remain in this feature directory rather than canonical docs.

## End-to-End Story Validation

The story is complete when a configured GitHub credential can be traced through indexing, publish, readiness, and validation paths without ambient host credentials; missing fine-grained permissions produce actionable redaction-safe diagnostics; and docs state when a GitHub App is the better automation choice.
