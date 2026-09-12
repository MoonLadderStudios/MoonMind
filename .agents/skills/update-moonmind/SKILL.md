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

Run `bash .agents/skills/update-moonmind/scripts/run-update-moonmind.sh --repo <deployment-checkout> --branch <branch>` (defaults: current directory and `main`). The portable script requires Python 3.10+, Git, Bash and Docker Compose V2. It checks these before fetching or changing deployment state. `tools/update-moonmind.sh` invokes this same entrypoint.

## Release authority and completion

The entrypoint fetches the selected branch without checking out or resetting local files. It resolves the exact source SHA to its published `sha-<commit>` image, verifies the image's source-revision label, and pins the repository digest. An unpublished image is an actionable unavailable release; never substitute `latest` or rebuild a different source under that identity.

The selected image supplies the canonical Compose definition, application code, migrations, portable Skills and release controller. Deployment-owned `.env`, interfaces, authentication and explicit configuration retain their existing authority. The image-owned controller is the portable semantic entrypoint for both this Skill and MoonMind's deployment tool. Docker, durable state storage and Temporal supply the execution substrate.

The controller records an immutable submission, starts one named updater with durable ownership, qualifies every affected worker queue with a pinned canary, promotes routing with a compare-and-set operation, reconciles the installed fleet, and drains temporary workers. The updater can replace the deployment-control service that launched it. A terminal release receipt and verified installed readiness establish completion. An image pull, process exit, or successful container start alone does not.

## Recovery

If the caller disappears, resume the printed submission with `--resume <submission-id>`. Keep its original image and inputs. Inspect the durable result before retrying any side effect; preserve primary deployment success if only cleanup remains. Report the exact unfinished phase and its recorded recovery owner when bounded recovery exhausts.

## Options

Optional arguments are `--compose-project <name>`, `--image-repository <repository>`, and `--dry-run` (show the intended release operation without fetching or deploying). The deployment-owned `docker-compose.override.yaml` (or `.yml`) accompanies the image's base configuration. Individual service restarts, live source overlays and source-only rebuilds are development operations and are outside this release contract.

Protected operator URLs require an existing authorized credential in the deployment-owned `deploy/state/operator-http-headers.json`, mapping each exact operator origin to its issued `Cookie` and/or `Authorization` header. Preserve this file as secret material outside Git. The updater sends credentials only to that origin, never mints a session or changes identity, and stops before replacement when authentication cannot be verified. Trusted-proxy identity remains owned by the proxy; do not supply asserted-user or forwarded headers.
