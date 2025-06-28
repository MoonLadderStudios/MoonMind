#!/usr/bin/env python3
"""
Example client for the Model Context Protocol implementation in MoonMind.
This demonstrates how an agent like OpenHands could interact with the MoonMind API.
"""

import requests
import sys
import os

# Default to localhost, but allow override via environment variable
API_BASE_URL = os.environ.get("MOONMIND_API_URL", "http://localhost:8000")


def send_context_request(messages, model="gemini-pro"):
    """
    Send a request to the Model Context Protocol endpoint.

    Args:
        messages: List of message objects with role and content
        model: The model to use for generation

    Returns:
        The response from the API
    """
    url = f"{API_BASE_URL}/context"

    payload = {
        "messages": messages,
        "model": model,
        "temperature": 0.7,
        "max_tokens": 1000,
        "metadata": {"source": "example_client"},
    }

    headers = {"Content-Type": "application/json"}

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

    return response.json()


def main():
    # Example conversation
    messages = [
        {"role": "system", "content": "You are a helpful AI assistant."},
        {
            "role": "user",
            "content": "What are the key features of the Model Context Protocol?",
        },
    ]

    # Specify model if provided as command line argument
    model = "gemini-pro"
    if len(sys.argv) > 1:
        model = sys.argv[1]

    print(f"Sending request to {API_BASE_URL}/context using model {model}...")
    response = send_context_request(messages, model)

    if response:
        print("\nResponse:")
        print(f"ID: {response['id']}")
        print(f"Model: {response['model']}")
        print(f"Created at: {response['created_at']}")
        print("\nContent:")
        print(response["content"])

        if "metadata" in response and "usage" in response["metadata"]:
            usage = response["metadata"]["usage"]
            print("\nToken Usage:")
            print(f"Prompt tokens: {usage.get('prompt_tokens', 'N/A')}")
            print(f"Completion tokens: {usage.get('completion_tokens', 'N/A')}")
            print(f"Total tokens: {usage.get('total_tokens', 'N/A')}")


if __name__ == "__main__":
    main()
