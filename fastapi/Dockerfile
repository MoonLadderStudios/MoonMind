FROM python:3.12-slim-bookworm

# Set working directory in the container
WORKDIR /app

# Install tesseract-ocr and required languages
RUN apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-eng

# Install hot reloading library
RUN pip install watchfiles

# Copy requirements and install dependencies
COPY fastapi/requirements.txt .
RUN pip install -r requirements.txt

# Expose the port the API will run on
EXPOSE 8000

# Run the FastAPI app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
