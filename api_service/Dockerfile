FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-eng && \
    rm -rf /var/lib/apt/lists/*

# Install hot reloading library
RUN pip install watchfiles

# Copy project configuration and core package code FIRST
COPY pyproject.toml /app/pyproject.toml
COPY moonmind /app/moonmind/
# Add any other files/directories needed for 'pip install /app' if they are part of the project structure
# defined in pyproject.toml (e.g. setup.cfg, MANIFEST.in if they exist and are used)

# Install the project and its dependencies from pyproject.toml
# This assumes 'moonmind' is the primary package to be installed, as suggested by pyproject.toml's find settings.
RUN pip install /app

# Copy other application-specific code that might not be part of the installable package
# or are meant for development overrides.
COPY api_service/api /app/api/
COPY api_service/main.py /app/main.py
# Note: Removed COPY api_service/requirements.txt and the corresponding pip install command.

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
