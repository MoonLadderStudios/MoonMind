FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-eng && \
    rm -rf /var/lib/apt/lists/*

# Install hot reloading library
RUN pip install watchfiles

# Copy pyproject.toml first for better Docker layer caching
COPY pyproject.toml /app/pyproject.toml

# Install dependencies from pyproject.toml
RUN pip install -e .
COPY moonmind /app/moonmind/
COPY api_service/api /app/api/
COPY api_service/main.py /app/main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
