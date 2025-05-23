#!/bin/sh

# Install curl
apt-get update && apt-get install -y curl

# Start the Ollama server in the background
ollama serve 2>&1 &

# Save the background process PID
SERVER_PID=$!

# Give the server a moment to start listening
sleep 3

# Determine which model to load
OLLAMA_MODEL_TYPE=${OLLAMA_MODEL_TYPE:-chat} # Default to chat if not set

if [ "${OLLAMA_MODEL_TYPE}" = "chat" ]; then
  if [ -z "${OLLAMA_CHAT_MODEL}" ]; then
    echo "Error: OLLAMA_CHAT_MODEL is not set for model type 'chat'." >&2
    exit 1
  fi
  MODEL_TO_LOAD="${OLLAMA_CHAT_MODEL}"
elif [ "${OLLAMA_MODEL_TYPE}" = "embedding" ]; then
  if [ -z "${OLLAMA_EMBEDDING_MODEL}" ]; then
    echo "Error: OLLAMA_EMBEDDING_MODEL is not set for model type 'embedding'." >&2
    exit 1
  fi
  MODEL_TO_LOAD="${OLLAMA_EMBEDDING_MODEL}"
else
  echo "Invalid OLLAMA_MODEL_TYPE: ${OLLAMA_MODEL_TYPE}. Defaulting to chat model." >&2
  if [ -z "${OLLAMA_CHAT_MODEL}" ]; then
    echo "Error: OLLAMA_CHAT_MODEL is not set for default model type 'chat'." >&2
    exit 1
  fi
  MODEL_TO_LOAD="${OLLAMA_CHAT_MODEL}"
fi

if [ -z "${MODEL_TO_LOAD}" ]; then
  echo "Error: No model specified to load." >&2
  exit 1
fi

echo "Attempting to pull model: ${MODEL_TO_LOAD}..."
if ! ollama pull "${MODEL_TO_LOAD}"; then
  echo "Error: Failed to pull model '${MODEL_TO_LOAD}'. Please ensure it is a valid and available model name." >&2
  # Decide if to exit or try to proceed. For now, let's exit.
  exit 1
fi

echo "Triggering model load for: ${MODEL_TO_LOAD}"
# Trigger a request that causes Ollama to load the desired model
# The empty prompt "" is enough to force a load
# Adding a check for curl command success
if ! curl -s -X POST http://localhost:11434/api/generate \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${MODEL_TO_LOAD}\",\"prompt\":\"\"}"; then
  echo "Error: curl command failed to trigger model load for ${MODEL_TO_LOAD}." >&2
  # Optionally, you might want to check Ollama server logs or status
  # For now, exiting, but this depends on desired robustness
  # exit 1 # Commenting out exit here as server might be running but model load failed.
fi

echo "Ollama server started with PID ${SERVER_PID}. Model ${MODEL_TO_LOAD} pre-loading initiated."

# Now, wait for the Ollama server so that the container remains alive
wait $SERVER_PID
