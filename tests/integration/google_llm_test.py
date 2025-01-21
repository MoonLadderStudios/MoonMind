import os

import pytest

from moonai.llms.google_llm import GoogleLLM


@pytest.mark.integration
def test_integration_invoke():
    """
    Integration test for GoogleLLM.invoke() method using the real Gemini model via the ChatGoogleGenerativeAI client.
    NOTE: This test will make real network calls and consume tokens. Make sure you have a valid GOOGLE_API_KEY set
          or explicitly pass it in. If no key is available, the test will be skipped.
    """
    api_key = os.getenv("GOOGLE_API_KEY", None)
    if not api_key:
        pytest.skip("No GOOGLE_API_KEY set; skipping integration test.")

    # Initialize the GoogleLLM with no explicit api_key to pull from env
    google_llm = GoogleLLM(model_name="gemini-2.0-flash-exp")

    # Minimal test messages
    messages = [
        ("system", "You are a helpful assistant."),
        ("human", "Hello! This is an integration test for Google's Gemini model. Can you respond briefly?")
    ]

    # Invoke the real API
    response = google_llm.invoke(messages)

    # Print the full response object and its components
    print("Integration test response (invoke):")
    print(f"  Content: {response.content}")
    print(f"  Additional kwargs: {response.additional_kwargs}")
    print(f"  Metadata: {response.response_metadata}")

    # Assert that a response was returned
    assert response is not None, "Expected a non-empty response from the API."


@pytest.mark.integration
def test_integration_stream():
    """
    Integration test for GoogleLLM.stream() method. This will yield partial responses from the real Gemini model.
    NOTE: This will also incur token usage. Skips if no GOOGLE_API_KEY set.
    """
    api_key = os.getenv("GOOGLE_API_KEY", None)
    if not api_key:
        pytest.skip("No GOOGLE_API_KEY set; skipping integration test.")

    google_llm = GoogleLLM(model_name="gemini-2.0-flash-exp")

    messages = [
        ("system", "You are a helpful assistant."),
        ("human", "Hello! Please respond with a short multi-sentence reply so we can test streaming.")
    ]

    # Stream the response from the real API
    stream_generator = google_llm.stream(messages)

    print("Integration test stream chunks:")
    chunks = []  # Collect chunks here

    for idx, chunk in enumerate(stream_generator, start=1):
        # Print each chunk with its details
        print(f"  Chunk {idx}:")
        print(f"    Content: {chunk.content}")
        print(f"    Additional kwargs: {chunk.additional_kwargs}")
        print(f"    Metadata: {chunk.response_metadata}")
        chunks.append(chunk)  # Collect chunk for later assertions

    # Assert that at least one chunk was returned
    assert len(chunks) > 0, "Expected at least one chunk from the streaming response."
