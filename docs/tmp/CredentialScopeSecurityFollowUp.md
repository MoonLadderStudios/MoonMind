# Credential scope security follow-up

Status: locally reviewed, partially corrected; no live deployment qualification.
Scope: the 17 historical high-severity GitHub/OAuth/provider-binding records
reviewed from integration base `8d28a98`. Single-operator configuration and
concurrent execution isolation are different boundaries.

## Corrections delivered locally

- `9d79b2aaa1e08191b1cf95427a4e2de0`: the managed adapter no longer declares
  ambient `GITHUB_TOKEN` authority on every Provider Profile. Scratch receives
  no token; a selected repository connection wins over ambient credentials.
  Repository and canonical `gh` requirements still derive normal authentication.
- `0898b9400e6c8191a4e1c6314c7e8824`: static Codex and Claude launches receive the
  selected credential volume name. Their observed container mount must match
  before readiness is probed on that exact container. Existing lifecycle cleanup
  handles rejection, and profile-owned credential state is retained.
- `7d2cb7b535048191922e8f4d6c9f1629`: the default offline Unreal workload no longer
  mounts the deployment's GHCR pull-auth volume. Current managed sessions already
  use a transient host-side Docker config that is removed after acquisition.
  Workspace/cache mounts and selected image behavior remain intact.

## Open: GitHub delivery and confinement

Existing owner: repository bound-access/runtime delivery in
[#4011](https://github.com/MoonLadderStudios/MoonMind/issues/4011), coordinated by
[Repository Access and Workspace Decoupling](RepositoryAccessAndWorkspaceDecouplingPlan.md).
The [canonical design](../RepositoryAccessAndWorkspaceDesign.md#quality-003-routing-and-confinement-are-separate-guarantees)
already separates routing from arbitrary-code confinement.

- `e0936505331081919f47e84f9650d790` and
  `6c924fa2c7388191836b900cb86d1eaa`: generic host materialization and the surviving
  profile-bound coordinator still resolve the broad deployment credential through
  `resolve_github_credential` when GitHub tooling or cloning is needed. Repository
  names passed as metadata do not reduce token scope. Preserve ordinary authored
  `gh` requirements; wire the existing selected authority through acquisition,
  readiness, transport, and cleanup rather than add a second allowlist or require
  a trusted Skill merely to use GitHub.
- `78b468b8afb48191957e5adf0f1ab11e`,
  `faad90f976748191aae5344507537adb`,
  `5063f45893608191b020f8d77a87196a`, and
  `f4d854e670a48191957483b9f3e014c2`: selected GitHub tokens still reach Codex/Claude
  process or session environments for nested-shell reliability. Removing that
  environment alone cannot establish confinement because token-returning helpers
  expose the same authority. Qualify scope-limited credentials or the existing
  design's supported credential-exposing/mediated execution policy across real
  CLI operations and recovery, without silently disabling working GitHub tasks.

Acceptance: selected connection B with ambient A uses only B throughout a real
host/CLI journey; scratch acquires nothing; declared tools remain functional;
failed or revoked selected authority never falls back; broad PAT exposure is
reported honestly rather than described as repository confinement.

## Open: concurrent-run broker isolation

- `18566cc8a7b081919345b595c5a3b723` and
  `1afd486673d881918fbf5ada79ce3e2e`: `GitHubAuthBrokerManager` serves a token to any
  connected client requesting `github_token`. Shared workspace mounts and equal
  numeric UIDs permit another run to reach its socket; direct subprocesses also
  share an OS identity. File modes and deterministic socket names do not isolate
  those peers. A nonce stored in same-UID-readable helper files is not a fix.

Continue through the existing managed-runtime/container workspace owners with
run-private filesystem/process visibility and broker reachability. Preserve
active sessions and recoverable work while transitioning shared mounts. Prove
that run A cannot read B's support files, process environment, or socket, while
B's real `git`/`gh`, retries, and cleanup keep working. This review did not run a
live two-container attack: this environment has no Docker CLI and cannot create
Unix sockets, including after an escalated test attempt.

## No-code conclusions

- `e96f8882ae288191a004838d391a3bef`: selected profile-owned Claude OAuth homes are
  intentionally writable under the existing generation/lease owner, as required
  for refresh and supported shared-host operation. Copying or freezing that home
  would contradict the owning OAuth design; unrelated-run exposure remains a
  separate isolation question.
- `aee3462823d081919ab7c8e6b5adcbb2`: the configured operator's model keys seed
  Provider Profiles by design. Selected profile materialization is intentional;
  the finding's independent low-privilege human/tenant premise is obsolete.
- `0b9f9c6888288191b1b87d38eaaff989` and
  `58c6558090048191bed706a669408e59`: the old auth-profile routes are gone; current
  launch discards the legacy `MANAGED_API_KEY_REF` fields, and surviving
  `secret_refs` come from operator-managed Provider Profiles. Their settings API
  uses strict operator-session authentication and profile settings permissions,
  not a scoped execution bearer. Arbitrary internal reference resolution is not
  itself a current untrusted public entrypoint.
- `83e6e41359948191b0f595ce04a6a556`: current selected-connection resolution fails
  closed when configured sources fail; only an unrecorded default uses the
  documented deployment declaration. The adapter bypass of selection was fixed
  separately above.
- `07a1d46c1bcc8191aaa68a1ef91244da`: current launch scrubs `GH_TOKEN`,
  `WORKFLOW_GITHUB_TOKEN`, and non-selected `GITHUB_TOKEN`. The old unrecognized
  `GH_TOKEN` leak is not reproduced on the current producer/consumer path.

## Verification

Observed red/green regressions cover adapter-to-launcher scratch leakage,
selected-B/ambient-A replacement, canonical `gh` requirement preservation,
static Codex volume selection, rejection of substituted/missing/nested mount
observations, and default Unreal launch argument credential exclusion.

- Managed adapter file plus affected launcher cases: 104 passed.
- OAuth lifecycle, static Claude qualification, workload launcher files:
  299 passed, 3 skipped (required writable `/home/app/.cache` layout unavailable).
- Existing session GHCR/scratch/connection and selected resolver cases: 18 passed.
- Full managed credential resolver file: 44 passed.
- Python compilation and `git diff --check`: passed.

These are overlapping targeted runs, not a unique-test total. Docker command
boundaries were exercised through recorded responses; no live Docker/provider
qualification, CI, publication, or deployment was performed. Socket transport
could not run here; selection regressions capture the real launcher's broker and
process arguments instead. No external finding status was changed.
