FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-eng git curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install watchfiles

# Copy pyproject.toml and README.md first for better Docker layer caching
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md

# Create empty directories to satisfy pip install
RUN mkdir -p /app/moonmind /app/api_service
RUN touch /app/moonmind/__init__.py /app/api_service/__init__.py

# Install dependencies first (this layer will be cached as long as pyproject.toml doesn't change)
RUN pip install -e .
RUN pip install readmeai~=0.6.3 --no-deps

# Copy source code AFTER installing dependencies
# This ensures that code changes don't invalidate the dependency cache
COPY moonmind /app/moonmind/
COPY api_service /app/api_service/

RUN chmod +x /app/api_service/entrypoint.sh && \
    sed -i 's/\r$//' /app/api_service/entrypoint.sh

EXPOSE 5000

CMD ["/app/api_service/entrypoint.sh"]
