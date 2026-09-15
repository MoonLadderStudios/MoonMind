# Contributing to MoonMind

Thanks for your interest in improving MoonMind. Issues and pull requests are welcome.

AI-assisted contributions are welcome, but contributors are responsible for reviewing, understanding, and testing everything they submit.

For substantial changes, please open an issue first so we can agree on the approach before implementation begins.

Do not include secrets, credentials, private data, private configuration, or unredacted logs in issues or pull requests.

## Development and testing

Clone the repository and initialize its submodules at their recorded commits:

```bash
git clone https://github.com/MoonLadderStudios/MoonMind.git
cd MoonMind
git submodule update --init --checkout -- moonspec omnigent
```

This checks out the commits recorded in the parent repository. It does not
advance dependencies to upstream (`--remote`), and it does not initialize
anything outside the two tracked submodules. To verify a clean checkout
without touching your working tree, run
`python tools/verify_clean_checkout.py` (see also
`python tools/verify_clean_checkout.py --help` for the disposable-clone path).

Start the local application:

```bash
docker compose up -d
```

Open [http://localhost:7000](http://localhost:7000) to use the dashboard.

Run the relevant automated checks before opening a pull request:

```bash
# Unit tests
./tools/test_unit_docker.sh

# Integration tests for cross-service or infrastructure changes
./tools/test_integration.sh
```

After the first unit-test run, use `./tools/test_unit_docker.sh --no-build` for faster repeat runs.

Use Test-Driven Development: write or update a behavioral test, observe the expected failure, implement the smallest correct change, then refactor with tests green. Behavior-preserving refactors start with passing coverage. Documentation-only changes do not require artificial failing tests.

Inside a MoonMind-managed workflow, use `moonmind container python-tests <pytest paths or node ids>` instead of the host Docker wrappers above. Runner and CI details are in [Pre-Commit Workflow](docs/Development/PreCommitWorkflow.md). Tests requiring live third-party provider credentials are not required unless a maintainer asks for them.

## Troubleshooting test environments

- Frontend `vitest` in a colon-bearing workspace path (for example a
  managed-agent run directory such as `/work/agent_jobs/workspaces/mm:...`)
  can fail with `ERR_MODULE_NOT_FOUND` because Node's ESM loader treats the
  `mm:` segment as a URL scheme. Work around it by copying the build inputs
  to a colon-free path and running there (`mkdir -p /tmp/mmbuild && cp -a
  node_modules frontend package.json package-lock.json /tmp/mmbuild/`, then
  `./node_modules/.bin/vitest run --config frontend/vite.config.ts`).
- `tests/unit/api/routers/test_executions.py::test_describe_execution_*`
  failures shaped as `IllegalStateChangeError: Method 'close()' can't be
  called here` are a known async-session/event-loop fixture artifact in some
  workspaces and reproduce on `main` in a clean worktree. Before treating
  such a failure as a regression, reproduce it on `main`; do not run two
  pytest processes over the same test files concurrently.

Local bytecode caches (`__pycache__/`, `*.pyc`) are ignored build output, not
repository defects. Optional cleanup is preview-first and scoped to the
repository: run `python tools/cleanup_dev_caches.py --preview` to list
targets, and only with an explicit `--apply` to remove them. Cleanup never
follows symlinks, never descends into submodule checkouts, and never deletes
tracked or uncommitted source content.

## Pull requests

Branch from `main`, complete the pull request template, and include tests or documentation when relevant. Include screenshots or a short recording for dashboard changes.

## Getting help

Open a GitHub issue for bugs, feature requests, or questions about a proposed contribution.
