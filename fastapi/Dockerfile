FROM python:3.12-slim-bookworm

# Set working directory in the container
WORKDIR /app

# Install hot reloading library
RUN pip install watchfiles

# Copy requirements and install dependencies
COPY fastapi/requirements.txt .
RUN pip install -r requirements.txt

# Expose the port the API will run on
EXPOSE 8000

# Run the FastAPI app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
