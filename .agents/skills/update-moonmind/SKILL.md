---
name: update-moonmind
description: Qualify and promote one immutable MoonMind release through a durable updater, preserving in-flight work and operator access.
metadata:
  required-capabilities:
    - git
    - docker
    - python3
---

# Update MoonMind

## Invocation

Establish the portable path before running the entrypoint:

```bash
UPDATE_MOONMIND_SKILL_DIR="${UPDATE_MOONMIND_SKILL_DIR:-${MOONMIND_ACTIVE_SKILLS_DIR:+$MOONMIND_ACTIVE_SKILLS_DIR/update-moonmind}}"
test -n "$UPDATE_MOONMIND_SKILL_DIR" && test -f "$UPDATE_MOONMIND_SKILL_DIR/SKILL.md"
```

Inside MoonMind, `MOONMIND_ACTIVE_SKILLS_DIR` is always set and the entrypoint
resolves from it; a checked-in `.agents/skills` directory must never shadow
the selected snapshot. Outside MoonMind, set `UPDATE_MOONMIND_SKILL_DIR` to
the directory containing this `SKILL.md` (no MoonMind-only environment
variables required). Run the update entrypoint exclusively from the resolved
Skill directory: `bash "$UPDATE_MOONMIND_SKILL_DIR/scripts/run-update-moonmind.sh" --repo <deployment-checkout> --branch <branch>` (defaults: current directory and `main`). The portable script requires Python 3.10+, Git, Bash and Docker Compose V2. It checks these before fetching or changing deployment state. `tools/update-moonmind.sh` invokes this same entrypoint.

## Release authority and completion

The entrypoint fetches the selected branch without checking out or resetting local files. It walks the branch first-parent history (up to 20 commits) to the newest commit with a published `sha-<commit>` image, verifies that image's source-revision label, and pins the repository digest. A tip commit with no published image yet (for example a just-merged commit whose publish workflow is still running) is skipped with a printed notice naming the selected ancestor; only when no ancestor has a published image is the unavailable release actionable. Never substitute `latest` or rebuild a different source under that identity.

The selected image supplies the canonical Compose definition, application code, migrations, portable Skills and release controller. Deployment-owned `.env`, interfaces, authentication and explicit configuration retain their existing authority. The image-owned controller is the portable semantic entrypoint for both this Skill and MoonMind's deployment tool. Docker, durable state storage and Temporal supply the execution substrate.

The controller records an immutable submission, starts one named updater with durable ownership, qualifies every affected worker queue with a pinned canary, promotes routing with a compare-and-set operation, reconciles the installed fleet, migrates the singular Omnigent release (server/host digests, launch policy versions, recurring schedule admissions) to the resolved digests, and drains temporary workers. The updater can replace the deployment-control service that launched it. A terminal release receipt and verified installed readiness establish completion. An image pull, process exit, or successful container start alone does not.

## Recovery

If the caller disappears, resume the printed submission with `--resume <submission-id>`. Keep its original image and inputs. Inspect the durable result before retrying any side effect; preserve primary deployment success if only cleanup remains. Report the exact unfinished phase and its recorded recovery owner when bounded recovery exhausts.

## Options

Optional arguments are `--compose-project <name>`, `--image-repository <repository>`, and `--dry-run` (show the intended release operation without fetching or deploying). A `--dry-run` preview never establishes completion: it writes no submission and proves nothing about the installed deployment. The deployment-owned `docker-compose.override.yaml` (or `.yml`) accompanies the image's base configuration.

`--local-build` is an explicit development-only escape hatch for exercising an
unpublished working tree (for example a feature branch awaiting its published
image). It recreates the stack on the repo's live-source development overlay,
writes no release submission, and claims no digest; it is never an immutable
release and must not be used for promotion or qualification. It preserves the
deployment-owned `.env`, requires published bindings to be unchanged
afterwards, and verifies each `--operator-url` health check. Roll back with a
plain `docker compose up -d`. Individual service restarts and source-only
image rebuilds remain outside this contract.

Use `--operator-url <existing-origin>` (repeatable) to declare the installed dashboard/API addresses for verification, especially for wildcard bindings without an authentication base URL. The declaration belongs to the immutable release submission and does not modify `.env`, authentication, or published bindings. Use addresses resolvable from the Docker backend, such as a full VPN hostname. Resume retains the original addresses. A configured `MOONMIND_PUBLIC_BASE_URL` remains a required verification target as well.

Protected operator URLs require an existing authorized credential in the deployment-owned `deploy/state/operator-http-headers.json`, mapping each exact operator origin to its issued `Cookie` and/or `Authorization` header. Preserve this file as secret material outside Git. The updater sends credentials only to that origin, never mints a session or changes identity, and stops before replacement when authentication cannot be verified. Trusted-proxy identity remains owned by the proxy; do not supply asserted-user or forwarded headers.
