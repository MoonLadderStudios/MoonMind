# Model Context Protocol in MoonMind

This document describes how MoonMind implements the Model Context Protocol, allowing it to act as a server that OpenHands and other agents can make client requests to.

## Overview

The Model Context Protocol is a standardized way for AI agents to communicate with language models. MoonMind implements this protocol to provide a consistent interface for agents to interact with various language models supported by MoonMind.

## API Endpoint

The Model Context Protocol is exposed via the `/context` endpoint. This endpoint accepts POST requests with a JSON payload containing messages and other parameters.

## Request Format

```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful AI assistant."
    },
    {
      "role": "user",
      "content": "What are the key features of the Model Context Protocol?"
    }
  ],
  "model": "gpt-4o", // Can be any supported model, e.g., "gemini-pro", "gpt-3.5-turbo"
  "temperature": 0.7,
  "max_tokens": 1000,
  "stream": false,
  "metadata": {
    "source": "example_client"
  }
}
```

### Request Parameters

- `messages`: An array of message objects, each with a `role` and `content`. Roles can be "system", "user", or "assistant".
- `model`: The model to use for generation (e.g., "gemini-pro", "gpt-4o", "gpt-3.5-turbo"). The server will route to the appropriate provider based on the model specified.
- `temperature`: Controls the randomness of the output. Higher values (e.g., 0.8) make the output more random, while lower values (e.g., 0.2) make it more deterministic.
- `max_tokens`: The maximum number of tokens to generate.
- `stream`: Whether to stream the response (not currently implemented).
- `metadata`: Additional metadata to include with the request.

## Response Format

```json
{
  "id": "ctx-1234567890abcdef",
  "content": "The Model Context Protocol is a standardized interface...",
  "model": "gpt-4o", // Reflects the model used for the response
  "created_at": 1621234567,
  "metadata": {
    "usage": {
      "prompt_tokens": 42,
      "completion_tokens": 128,
      "total_tokens": 170
    }
  }
}
```

### Response Parameters

- `id`: A unique identifier for the response.
- `content`: The generated text.
- `model`: The model used for generation (will match the request or the provider's specific model ID).
- `created_at`: The timestamp when the response was created.
- `metadata`: Additional metadata, including token usage information.

## Docker Configuration

The MoonMind API container is configured to act as a Model Context Protocol server with the following settings in `docker-compose.yaml`:

```yaml
environment:
  - MODEL_CONTEXT_PROTOCOL_ENABLED=true
  - MODEL_CONTEXT_PROTOCOL_PORT=5000
  - MODEL_CONTEXT_PROTOCOL_HOST=0.0.0.0
labels:
  - "ai.model.context.protocol.version=0.1"
  - "ai.model.context.protocol.endpoint=/context"
```

## Example Client

An example client is provided in `/examples/context_protocol_client.py` to demonstrate how to interact with the Model Context Protocol endpoint.

To use the example client:

```bash
# Run with default model (as configured in the server, e.g., gemini-pro or gpt-3.5-turbo)
python examples/context_protocol_client.py

# Run with a specific Google model
python examples/context_protocol_client.py gemini-pro-vision

# Run with a specific OpenAI model
python examples/context_protocol_client.py gpt-4o
```

## Using with OpenHands

OpenHands and other agents can connect to MoonMind's Model Context Protocol server by configuring their client to point to the MoonMind API endpoint:

```
http://api:5000/context
```

If accessing from outside the Docker network, use:

```
http://localhost:5000/context
```