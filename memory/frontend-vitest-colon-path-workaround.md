---
name: frontend-vitest-colon-path-workaround
description: Frontend vitest fails in managed-agent workspaces because the job dir name contains a colon; run from a colon-free copy
metadata:
  type: project
---

In MoonMind-managed agent workspaces the run directory is named with the job UUID and contains a colon, e.g. `/work/agent_jobs/workspaces/mm:3d87...`. Node's ESM loader treats the `mm:` segment as a URL scheme, so `vitest run` (vite 8 / vitest 4, `frontend/vite.config.ts`) mangles every absolute test-file import down to `/frontend/src/...` and fails with `ERR_MODULE_NOT_FOUND` / "0 test". `preserveSymlinks` and explicit `--root` do not help (vite resolves the realpath).

**Why:** the colon in the workspace path, not any test or config defect.

**How to apply:** copy the build inputs to a colon-free path and run there:
`mkdir -p /tmp/mmbuild && cp -a node_modules frontend package.json package-lock.json /tmp/mmbuild/ && cd /tmp/mmbuild && ./node_modules/.bin/vitest run --config frontend/vite.config.ts <name-filter>`.
`npm ci --no-fund --no-audit` first if `node_modules` is missing. `npm run ui:test` may also report "vitest: not found" in this env — invoke `./node_modules/.bin/vitest` directly. tsc/eslint work the same way from the copy.
