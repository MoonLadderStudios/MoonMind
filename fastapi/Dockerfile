FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-eng && \
    rm -rf /var/lib/apt/lists/*

# Install hot reloading library
RUN pip install watchfiles

COPY fastapi/requirements.txt /app/requirements.txt

RUN pip install -r /app/requirements.txt

# Copy directories which will be overriden by mounts during development
COPY pyproject.toml /app/pyproject.toml
COPY moonmind /app/moonmind/
COPY fastapi/api /app/api/
COPY fastapi/main.py /app/main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
