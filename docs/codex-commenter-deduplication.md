# Codex Commenter De-duplication

## Problem

The automated Codex commenter action was posting multiple duplicate `@codex fix build failures` comments on PRs when CI failed multiple times. This happened because:

1. **Concurrency group included workflow run IDs** - Each retry/re-run of the pytest workflow had a different `workflow_run.id`, creating separate concurrency groups
2. **Race conditions** - Multiple workflow runs for the same PR could execute in parallel

Example from [PR #198](https://github.com/MoonLadderStudios/MoonMind/pull/198):
- Three `@codex fix build failures` comments posted simultaneously
- All triggered by the same commit/build failure
- Created confusion and wasted bot resources

## Solution

### 1. Fixed Concurrency Grouping

**Before:**
```yaml
concurrency:
  group: codex-commenter-${{ ... || github.event.workflow_run.id || github.run_id }}
```

**After:**
```yaml
concurrency:
  group: codex-commenter-${{ ... || github.event.workflow_run.head_sha || 'global' }}
```

**Impact:** Now all workflow runs for the same PR/commit share the same concurrency group. Only one can run at a time, and newer runs cancel older ones.

### 2. Added Exact Match Detection

Added logic to detect if the exact same comment was already posted since the latest commit:

```javascript
const exactMatchInRecentComments = commentsAfterCutoff.some(comment => {
  const body = (comment.body || '').toLowerCase();
  const cleanBody = body.replace(/<!--[^>]*-->/g, '').trim();
  return cleanBody === normalizedCommentBodyLower;
});

if (exactMatchInRecentComments) {
  console.log(`Exact match found for "${normalizedCommentBody}" since latest commit. Skipping duplicate.`);
  return;
}
```

**Impact:** Even if concurrency fails, the action will detect existing identical comments and skip posting.

## How It Works Now

### Concurrency Hierarchy

The action groups workflow runs by (in priority order):
1. PR number (if available)
2. Issue number (if available)
3. Workflow run head SHA (for workflow_run events)
4. PR head SHA (for direct PR events)
5. `'global'` (fallback)

Example concurrency groups:
- `codex-commenter-198` (PR #198)
- `codex-commenter-abc123def` (commit SHA)
- `codex-commenter-global` (fallback)

### Multiple Protection Layers

1. **Concurrency control** (`cancel-in-progress: true`)
   - Newer runs cancel older ones in the same group
   - Only one run per PR/commit at a time

2. **SHA-based comment tracking** (existing)
   - Comments include `<!-- codex:purpose sha=abc123 -->` markers
   - Updates existing comments instead of posting new ones

3. **Recent fix command detection** (existing)
   - Checks if `@codex fix` was already posted since latest commit
   - Skips if found

4. **Exact match detection** (new)
   - Checks for identical comment text
   - Final safety net against duplicates

5. **Rate limiting** (existing)
   - 5-minute window prevents spam
   - Tracks by purpose (build-fix, comment-fix, review)

## Testing

### Scenario 1: Single Failure
```
✅ Build fails → Action posts "@codex fix build failures"
✅ Build fails again (before fix) → Skipped (exact match found)
```

### Scenario 2: Multiple Rapid Failures
```
✅ Build fails (trigger 1) → Run 1 starts
✅ Build fails (trigger 2) → Run 2 starts, cancels Run 1
✅ Build fails (trigger 3) → Run 3 starts, cancels Run 2
✅ Only Run 3 completes → Posts single comment
```

### Scenario 3: New Commit
```
✅ Commit A: Build fails → "@codex fix build failures" posted
✅ Commit B: Build fails → New "@codex fix build failures" posted (different commit)
```

## Monitoring

Check workflow logs for these messages:

**Concurrency working:**
```
Found N other active run(s) of this workflow for this PR. Exiting to avoid duplicates.
```

**SHA tracking working:**
```
Existing Codex comment already matches desired message for <sha>. Skipping.
```

**Exact match working:**
```
Exact match found for "@codex fix build failures" since latest commit. Skipping duplicate.
```

**Rate limiting working:**
```
Recent Codex bot activity detected (<5m) for purpose 'build-fix'. Skipping to avoid spam.
```

## Configuration

The action is configured in `.github/workflows/codex-commenter.yml`:

- **Triggers**: `workflow_run` (on test completion), `issue_comment`, `pull_request_review_comment`, `pull_request_review`
- **Concurrency**: Per PR/commit with `cancel-in-progress: true`
- **Rate limit**: 5 minutes per purpose
- **Comment window**: Only considers comments after latest commit

## Troubleshooting

### Still seeing duplicates?

1. **Check concurrency group in logs:**
   ```
   Look for: "group: codex-commenter-XXX"
   ```
   All runs for the same PR should have the same XXX value.

2. **Check timing:**
   ```
   Look for: "Found N other active run(s)..."
   ```
   Should see this if multiple runs start simultaneously.

3. **Check comment detection:**
   ```
   Look for: "Exact match found..." or "Existing Codex comment..."
   ```
   Should see one of these if comment already exists.

### Comments not posting?

1. **Check if PR is open:**
   ```
   Look for: "PR is not open (state: closed). Skipping..."
   ```

2. **Check if review in progress:**
   ```
   Look for: "PR has 'eyes' reactions - someone is reviewing"
   ```

3. **Check rate limit:**
   ```
   Look for: "Recent Codex bot activity detected (<5m)..."
   ```

## Related Files

- `.github/workflows/codex-commenter.yml` - Main workflow
- `.github/workflows/pytest-unit-tests.yml` - Triggers the commenter
- `docs/pre-commit-integration.md` - Pre-commit checks (runs before tests)

## See Also

- [GitHub Actions Concurrency](https://docs.github.com/en/actions/using-jobs/using-concurrency)
- [Workflow Run Events](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#workflow_run)

