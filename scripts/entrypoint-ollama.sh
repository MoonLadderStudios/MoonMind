#!/bin/sh

# Install curl
apt-get update && apt-get install -y curl

# Start the Ollama server in the background
ollama serve 2>&1 &

# Save the background process PID
SERVER_PID=$!

# Give the server a moment to start listening
sleep 3

# Trigger a request that causes Ollama to load the desired model
# The empty prompt "" is enough to force a load
curl -s -X POST http://localhost:11434/api/generate \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${OLLAMA_MODEL}\",\"prompt\":\"\"}"

# Now, wait for the Ollama server so that the container remains alive
wait $SERVER_PID
