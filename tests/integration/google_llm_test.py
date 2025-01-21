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
    # Or pass `api_key=api_key` if you prefer to do so explicitly.
    google_llm = GoogleLLM(model_name="gemini-2.0-flash-exp")

    # Minimal test messages
    messages = [
        ("system", "You are a helpful assistant."),
        ("human", "Hello! This is an integration test for Google's Gemini model. Can you respond briefly?")
    ]

    # Invoke the real API
    response = google_llm.invoke(messages)
    print(f"Integration test response: {response}")

    # We expect a non-empty response
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

    stream_generator = google_llm.stream(messages)

    # Collect chunks for verification
    chunks = list(stream_generator)
    print("Integration test stream chunks:")
    for idx, chunk in enumerate(chunks, start=1):
        print(f"Chunk {idx}: {chunk}")

    # We expect at least one chunk
    assert len(chunks) > 0, "Expected at least one chunk from the streaming response."
