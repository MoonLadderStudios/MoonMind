# Standalone deployment controller image (MoonLadderStudios/MoonMind#4500).
#
# The controller is stdlib-only Python: it must run without the MoonMind
# application image, its API, DB, Temporal, artifact service, provider
# manager, Omnigent, or an LLM. Release configuration arrives as data.
FROM python:3.12-slim

WORKDIR /controller
COPY moonmind_controller/ /controller/moonmind_controller/

# The controller never replaces itself; it has no self-update path.
ENTRYPOINT ["python", "-m", "moonmind_controller"]
