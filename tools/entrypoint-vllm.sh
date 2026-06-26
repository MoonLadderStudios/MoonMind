#!/bin/sh

set -e # Exit immediately if a command exits with a non-zero status.

# Log current environment variables for debugging (optional)
echo "Starting VLLM OpenAI API Server with the following settings:"
echo "Model Name: ${MODEL_NAME}"
echo "Data Type: ${DTYPE}"
echo "GPU Memory Utilization: ${GPU_MEMORY_UTILIZATION}"
echo "HF_HOME: ${HF_HOME}"

# Launch the VLLM server
# Ensure HF_HOME is respected by Hugging Face tools within VLLM
export HF_HOME=${HF_HOME:-/root/.cache/huggingface}

python3 -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --port 8000 \
    --model "${MODEL_NAME}" \
    --dtype "${DTYPE}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --download-dir "${HF_HOME}/hub" # Explicitly set download dir, though VLLM often respects HF_HOME

echo "VLLM server started."
