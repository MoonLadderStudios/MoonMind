FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-eng && \
    rm -rf /var/lib/apt/lists/*

# Install hot reloading library
RUN pip install watchfiles

COPY api_service/requirements.txt /app/requirements.txt

RUN pip install -r /app/requirements.txt

# Diagnostic commands
RUN echo "==== PIP LIST OUTPUT ====" && \
    pip list && \
    echo "==== PIP SHOW llama-index-embeddings-google ====" && \
    pip show llama-index-embeddings-google && \
    echo "==== PYTHON SYS.PATH ====" && \
    python -c "import sys; print(sys.path)" && \
    echo "==== ATTEMPTING DIRECT IMPORT OF llama_index_embeddings_google ====" && \
    python -c "print('Attempting to import llama_index_embeddings_google directly...'); import llama_index_embeddings_google; print('Import successful. Path:', llama_index_embeddings_google.__file__)" && \
    echo "==== DIAGNOSTICS COMPLETE ===="

# Copy other project files (pyproject.toml, moonmind, api_service/api, api_service/main.py)
COPY pyproject.toml /app/pyproject.toml
COPY moonmind /app/moonmind/
COPY api_service/api /app/api/
COPY api_service/main.py /app/main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
