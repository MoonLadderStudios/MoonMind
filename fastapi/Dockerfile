FROM python:3.12-slim-bookworm

WORKDIR /app

# Install OS dependencies
RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-eng && \
    rm -rf /var/lib/apt/lists/*

# Install base Python dependencies from requirements.txt first
# These are external dependencies, not the project's own packages
COPY fastapi/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project definition and source code
# pyproject.toml is copied from the root of the build context
COPY pyproject.toml /app/pyproject.toml
# moonmind directory is copied from the root of the build context
COPY moonmind /app/moonmind/
# fastapi directory is copied from the root of the build context
COPY fastapi /app/fastapi/

# Ensure main.py for the CMD is at /app/main.py
# This main.py is originally located at fastapi/main.py in the source repo
COPY fastapi/main.py /app/main.py

# Install the project in editable mode using the pyproject.toml in /app
# This makes 'moonmind' and 'fastapi' packages importable from anywhere
RUN pip install --no-cache-dir -e /app

# Install hot reloading library (can be here or with other pip installs)
# If it's already in requirements.txt, this might be redundant but harmless.
RUN pip install --no-cache-dir watchfiles

EXPOSE 8000

# CMD refers to main:app, where main.py is now at /app/main.py
# and it can import 'moonmind' and 'fastapi' modules due to the editable install.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
