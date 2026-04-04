# Quickstart: Route Canonicalization

## Verification Steps

1. **Run unit tests**:
   ```bash
   ./tools/test_unit.sh
   ```
   All tests must pass, including new redirect tests and updated assertions.

2. **Manual verification** (optional):
   ```bash
   # Start the API service
   cd api_service && python -m uvicorn main:app --reload --port 8001
   
   # Verify redirects
   curl -I http://localhost:8001/tasks/create        # Expect 307 → /tasks/new
   curl -I http://localhost:8001/tasks/tasks-list    # Expect 307 → /tasks/list
   
   # Verify nav links
   curl http://localhost:8001/tasks/list | grep 'route-nav'
   # Tasks link should point to /tasks/list, not /tasks
   ```

3. **E2E test check**:
   Run E2E tests that navigate to `/tasks/create` and verify they still pass (Playwright follows redirects). Update any URL assertions that expect the old alias paths.
