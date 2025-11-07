# Research: Codex & Spec Kit Tooling Availability

## Decision 1: Codex CLI install source & version pin
- **Decision**: Install Codex CLI via the official npm package `@githubnext/codex-cli` during the Docker build, using a `CODEX_CLI_VERSION` build arg defaulting to a vetted semver tag (initially `0.6.x`) so releases can be bumped intentionally.
- **Rationale**: The npm distribution is the only widely documented delivery mechanism for the Codex CLI, keeps Node-based dependencies isolated, and allows deterministic builds by pinning versions. Installing during the Docker build means Celery workers inherit the binary without runtime downloads.
- **Alternatives considered**:
  - *Download prebuilt tarballs from GitHub Releases*: rejected because no official release artifacts are published yet and maintaining manual URLs would be brittle.
  - *Install via `pip`*: not supported by the Codex team; would require bundling an unofficial wrapper and add maintenance burden.
  - *Install at container runtime*: rejected because Spec workflow tasks must start instantly and sandboxed nodes often lack outbound network access.

## Decision 2: GitHub Spec Kit CLI installation approach
- **Decision**: Install the GitHub Spec Kit CLI from its npm package (`@githubnext/spec-kit`) within the Docker build, controlled by a `SPEC_KIT_VERSION` build arg so Spec platform operators can align the CLI version with repo expectations.
- **Rationale**: Spec Kit’s own documentation prescribes the npm CLI; using a versioned build arg keeps Celery workers consistent with other environments and simplifies upgrades (single Docker build). Installing globally exposes the `speckit` binary on PATH for both FastAPI and Celery processes.
- **Alternatives considered**:
  - *Vendoring source files into the repo*: increases maintenance overhead and risks divergence from upstream CLI behavior.
  - *Using `npx` to download on demand*: blocked by offline/headless Celery runs and increases per-task startup time.
  - *Building from source via Go/Rust releases*: no such releases exist; would delay adoption and complicate the toolchain.

## Decision 3: Managing `~/.codex/config.toml`
- **Decision**: During the Docker build, create a template file (`/etc/codex/config.toml`) with `approval_policy = "never"`, then copy/merge it into the worker user’s `~/.codex/config.toml` at entrypoint time using a small Python/TOML merge script that preserves existing keys while overwriting only the `approval_policy` value.
- **Rationale**: Writing directly to `~/.codex/config.toml` inside the image risks clobbering user-provided settings (tokens, endpoints). A merge script can be idempotent, handles the case where the file does not yet exist, and protects the single required key while respecting future Codex preferences.
- **Alternatives considered**:
  - *Overwrite the entire config file with a static template*: rejected because it could erase tokens or experimental settings captured by operators.
  - *Rely on runtime `codex config set approval_policy never`*: depends on CLI behavior that might change and would re-touch config on every invocation.
  - *Document manual steps only*: fails the requirement to ensure unattended Celery tasks never stall; automation is mandatory.

## Decision 4: Verification & health checks
- **Decision**: Extend worker bootstrap/health scripts to run `codex --version`, `codex login status`, and `speckit --help` once at startup, failing fast if binaries or the managed config are missing.
- **Rationale**: Early detection prevents the Celery queue from accepting jobs that are guaranteed to fail later, aligns with spec FR-005, and surfaces actionable errors in logs. Running the commands once avoids runtime cost while still confirming installations.
- **Alternatives considered**:
  - *Rely solely on Docker build success*: insufficient because credentials/config might be wiped between deployments.
  - *Run checks per task*: adds latency to every workflow and duplicates logs.
  - *Manual verification*: not scalable for unattended automation.

## Decision 5: Dependency best practices
- **Decision**: Keep Node/npm tooling confined to the Docker build stage by using a multi-stage build: install Node + CLIs in a builder layer, copy resulting binaries into the final runtime layer, and remove npm caches to minimize image size.
- **Rationale**: The runtime image primarily executes Python workloads; leaving Node/npm there increases attack surface and slows security scanning. Multi-stage builds keep the final layer slim while still packaging the needed CLIs.
- **Alternatives considered**:
  - *Install Node/npm in the final layer*: simpler but bloats the runtime image and increases patching surface.
  - *Use OS package managers (apt)*: Codex & Spec Kit CLIs are not available as apt packages, so this path would still require manual tarball management.
  - *Ship separate sidecar containers for the CLIs*: overkill for simple command-line utilities and complicates Celery task orchestration.
