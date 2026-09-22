# Standalone deployment controller image (MoonLadderStudios/MoonMind#4500).
#
# The controller is stdlib-only Python: it must run without the MoonMind
# application image, its API, DB, Temporal, artifact service, provider
# manager, Omnigent, or an LLM. Release configuration arrives as data.
FROM python:3.12-slim

# The production apply path shells out to `docker compose` subprocesses.
# Mounting /var/run/docker.sock provides no Docker CLI or Compose plugin,
# so the image ships both; without them every operation fails with
# FileNotFoundError before it can repair the target stack.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io docker-compose-plugin \
    && rm -rf /var/lib/apt/lists

WORKDIR /controller
COPY moonmind_controller/ /controller/moonmind_controller/

# The controller never replaces itself; it has no self-update path.
ENTRYPOINT ["python", "-m", "moonmind_controller"]
