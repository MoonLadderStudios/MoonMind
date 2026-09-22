# Standalone controller image: stdlib-only, no MoonMind application modules.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/controller
COPY mm_controller ./mm_controller

# The controller never replaces itself: no self-update tooling inside.
ENTRYPOINT ["python", "-m", "mm_controller.main"]
CMD ["serve", "--state-dir", "/state"]
