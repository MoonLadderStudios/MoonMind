# Contributing to MoonMind

Thanks for your interest in improving MoonMind. Issues and pull requests are welcome.

For larger changes, please open an issue first so maintainers and contributors can align on the approach before code is written.

Please do not include secrets, tokens, internal URLs, customer data, private configuration, private repository contents, or unredacted logs in issues, tests, examples, screenshots, or pull requests.

## AI-assisted contributions are welcome

You may use an AI coding workflow to prepare a contribution.

We review the final contribution, not the tool that helped create it. AI-assisted pull requests are welcome when they are:

- focused and reviewable
- linked to an issue or a clear problem statement
- manually reviewed by the contributor submitting the PR
- tested with the relevant commands
- safe with respect to secrets, credentials, sandboxing, Docker, network access, provider profiles, and publish paths
- explained clearly in the PR description

Low-effort generated rewrites, unrelated drive-by changes, fake validation claims, or PRs the submitter cannot explain may be closed or asked to revise.

## Development setup

Clone the repository and initialize submodules:

```bash
git clone https://github.com/MoonLadderStudios/MoonMind.git
cd MoonMind
git submodule update --init --recursive
```

Start the local stack:

```bash
docker compose up -d
```

Open [http://localhost:7000](http://localhost:7000), configure the provider credentials you need in Settings, and submit a workflow from the dashboard.

For Python/backend development, use a repository-local virtual environment when possible:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip uv
uv pip install -e ".[tests]"
```

For frontend work, install the JavaScript dependencies from the lockfile:

```bash
npm ci
```

## Common checks

Run the smallest validation that covers the change. Paste the exact commands and results into your pull request.

For Python/backend changes:

```bash
./tools/test_unit.sh --python-only
```

For frontend changes:

```bash
npm run frontend:ci
```

For focused frontend tests:

```bash
./tools/test_unit.sh --ui-args frontend/src/path/to/test.tsx
```

For changes that affect Docker, compose, database, migrations, integration tests, runtime infrastructure, artifacts, worker topology, live logs, or startup seeding:

```bash
./tools/test_integration.sh
```

Provider verification tests that require live third-party credentials are not required for normal community pull requests unless a maintainer specifically asks for them.

## Tests

A change that alters behavior should usually include a test. A bug fix should add or update a test that would have failed before the fix when practical.

Prefer the smallest focused test that covers the change. Use broader integration tests only when behavior genuinely crosses component boundaries.

Changes to Temporal workflows, activity signatures, signal/update names, serialized payload shapes, status normalization, runtime adapters, sandboxing, credentials, provider profiles, or publish paths need boundary-oriented coverage or a clear explanation of why that coverage is not applicable.

Pure refactors, formatting-only changes, docs-only changes, dependency bumps, type-only changes, and copy edits with no behavior change may not need new tests. Explain that in the PR when you mark test coverage as not applicable.

## Pull requests

- Branch from `main` and keep changes focused.
- Use one pull request per issue or coherent change.
- Fill in the pull request template.
- Include tests or docs when relevant.
- Include screenshots or a short recording for dashboard/UI behavior changes.
- Be honest about what validation you did and did not run.
- Call out risks around secrets, credentials, sandboxing, Docker, network access, provider profiles, billing-relevant settings, publish paths, migrations, and Temporal replay/in-flight compatibility.

Draft pull requests are welcome when you want early feedback. Ready-for-review pull requests should contain enough context and validation evidence for maintainers to review confidently.

## Getting help

Open a GitHub issue for actionable bugs and feature requests. Use the issue templates so maintainers have the information needed to reproduce, triage, and review the request.
