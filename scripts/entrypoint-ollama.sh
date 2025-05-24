#!/bin/sh

# Install curl
apt-get update && apt-get install -y curl

# Start the Ollama server in the background
ollama serve 2>&1 &

# Save the background process PID
SERVER_PID=$!

# Give the server a moment to start listening
sleep 3

# Determine models to load based on OLLAMA_MODES
OLLAMA_MODES=${OLLAMA_MODES:-chat} # Default to "chat" if not set
echo "Processing OLLAMA_MODES: ${OLLAMA_MODES}"

echo "${OLLAMA_MODES}" | tr ',' '\n' | while read -r MODE || [ -n "$MODE" ]; do
  # Trim whitespace from MODE (read -r should handle leading/trailing, but this is safer for internal spaces if any)
  MODE=$(echo "$MODE" | xargs)

  if [ "$MODE" = "chat" ]; then
    if [ -z "${OLLAMA_CHAT_MODEL}" ]; then
      echo "Warning: OLLAMA_CHAT_MODEL is not set. Skipping chat model." >&2
      continue
    fi
    MODEL_TO_PULL="${OLLAMA_CHAT_MODEL}"
    echo "Attempting to pull chat model: ${MODEL_TO_PULL}..."
    if ! ollama pull "${MODEL_TO_PULL}"; then
      echo "Error: Failed to pull chat model '${MODEL_TO_PULL}'. Skipping." >&2
      continue
    fi
    echo "Triggering model load for chat model: ${MODEL_TO_PULL}"
    if ! curl -s -X POST http://localhost:11434/api/generate \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"${MODEL_TO_PULL}\",\"prompt\":\"\"}"; then
      echo "Error: curl command failed for chat model '${MODEL_TO_PULL}'. It might not be loaded." >&2
    fi
  elif [ "$MODE" = "embed" ]; then
    if [ -z "${OLLAMA_EMBEDDINGS_MODEL}" ]; then
      echo "Warning: OLLAMA_EMBEDDINGS_MODEL is not set. Skipping embed model." >&2
      continue
    fi
    MODEL_TO_PULL="${OLLAMA_EMBEDDINGS_MODEL}"
    echo "Attempting to pull embed model: ${MODEL_TO_PULL}..."
    if ! ollama pull "${MODEL_TO_PULL}"; then
      echo "Error: Failed to pull embed model '${MODEL_TO_PULL}'. Skipping." >&2
      continue
    fi
    echo "Triggering model load for embed model: ${MODEL_TO_PULL}"
    if ! curl -s -X POST http://localhost:11434/api/generate \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"${MODEL_TO_PULL}\",\"prompt\":\"\"}"; then
      echo "Error: curl command failed for embed model '${MODEL_TO_PULL}'. It might not be loaded." >&2
    fi
  elif [ -n "$MODE" ]; then # If mode is not empty but unrecognized
    echo "Warning: Unknown mode '${MODE}' in OLLAMA_MODES. Skipping." >&2
  fi
done

echo "Ollama model loading process complete. Server (PID ${SERVER_PID}) continues to run."

# Now, wait for the Ollama server so that the container remains alive
wait $SERVER_PID
