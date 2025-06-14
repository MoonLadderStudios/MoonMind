FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-eng && \
    rm -rf /var/lib/apt/lists/*

# Install hot reloading library
RUN pip install watchfiles

# Copy pyproject.toml first for better Docker layer caching
COPY pyproject.toml /app/pyproject.toml
# Copy other necessary files for installation
COPY README.md /app/README.md
COPY moonmind /app/moonmind/
COPY api_service /app/api_service/

# Install dependencies from pyproject.toml, including api_service in editable mode
# This assumes pyproject.toml is set up to recognize 'api_service' as a package.
RUN pip install -e .

EXPOSE 8000

# Update CMD to correctly point to the app within the api_service package
CMD ["uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
