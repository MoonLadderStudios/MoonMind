# Research: Cursor CLI Phase 1

**Feature**: 088-cursor-cli-phase1
**Date**: 2026-03-20

## R1: Cursor CLI Binary Name Conflict

**Decision**: Rename installed binary from `agent` to `cursor-agent`
**Rationale**: The Cursor CLI binary name `agent` is extremely generic and would conflict with any other tool named `agent` on the PATH. Using `cursor-agent` provides clear namespacing.
**Alternatives considered**:
- Keep as `agent` with PATH precedence: Rejected — too fragile, breaks if other tools install an `agent` binary
- Symlink approach: Rejected — same conflict risk
- `cursor-cli`: Considered acceptable but `cursor-agent` better describes the tool's purpose

## R2: Auto-Update Behavior

**Decision**: Pin at build time, document auto-update as disabled by default in containers
**Rationale**: Docker images are immutable after build. The Cursor CLI's auto-update feature won't trigger in ephemeral containers because the binary is in a read-only layer (`/usr/local/bin/`). This is inherently deterministic without needing an explicit disable flag.
**Alternatives considered**:
- Runtime install + update: Rejected — non-deterministic, adds startup latency
- Explicit auto-update disable flag: Not available in current Cursor CLI docs

## R3: Auth Script Pattern Alignment

**Decision**: Follow `auth-gemini-volume.sh` pattern with `--api-key` replacing `--sync`
**Rationale**: Cursor CLI's primary auth mode for managed runtimes is API key (not OAuth file sync). The `--api-key` mode stores the key in the volume so the container can access it. `--login` and `--check` follow the same interactive/verify pattern.
**Alternatives considered**:
- Simpler script (check-only): Rejected — doesn't match existing script UX expectations
- Combined with existing auth scripts: Rejected — violates separation of concerns

## R4: Docker Compose Init Service Profile

**Decision**: Use `profiles: [init]` for `cursor-auth-init` service
**Rationale**: Auth init services should not run on every `docker compose up`. Using the `init` profile keeps them opt-in, consistent with how other init services should behave.
**Alternatives considered**:
- Run always with `restart: no`: Acceptable but noisy in compose logs
- Remove init service entirely: Rejected — need consistent volume provisioning pattern
