# test_google_llm.py

import os
from unittest.mock import MagicMock, patch

import pytest
from langchain_google_genai import ChatGoogleGenerativeAI

from moonai.llms.google_llm import GoogleLLM


@pytest.fixture
def mock_llm():
    """
    Fixture to create a mock ChatGoogleGenerativeAI instance.
    """
    with patch.object(ChatGoogleGenerativeAI, '__init__', return_value=None) as mock_init:
        with patch.object(ChatGoogleGenerativeAI, 'invoke') as mock_invoke, \
             patch.object(ChatGoogleGenerativeAI, 'stream') as mock_stream:

            # Set return_value or side_effects if needed
            mock_invoke.return_value = "Mocked AI Response"
            mock_stream.return_value = iter(["Chunk1", "Chunk2"])

            yield {
                "mock_init": mock_init,
                "mock_invoke": mock_invoke,
                "mock_stream": mock_stream
            }


def test_init_with_api_key(mock_llm):
    """
    Tests the initialization of GoogleLLM when an api_key is provided directly.
    """
    test_api_key = "test-key"
    google_llm = GoogleLLM(api_key=test_api_key, model_name="gemini-2.0-flash-exp")

    # Ensure ChatGoogleGenerativeAI __init__ is called with correct model.
    mock_llm["mock_init"].assert_called_once_with(model="gemini-2.0-flash-exp")

    assert google_llm.api_key == test_api_key
    assert google_llm.model_name == "gemini-2.0-flash-exp"


def test_init_with_env_api_key(monkeypatch, mock_llm):
    """
    Tests the initialization of GoogleLLM when api_key is not provided
    but we rely on GOOGLE_API_KEY in the environment.
    """
    monkeypatch.setenv("GOOGLE_API_KEY", "env-test-key")
    google_llm = GoogleLLM(model_name="gemini-2.0-flash-exp")  # no api_key passed

    # Ensure ChatGoogleGenerativeAI __init__ was called
    mock_llm["mock_init"].assert_called_once_with(model="gemini-2.0-flash-exp")

    assert google_llm.api_key == "env-test-key"
    assert google_llm.model_name == "gemini-2.0-flash-exp"


def test_set_api_key(mock_llm):
    """
    Tests setting a new API key after initialization.
    """
    google_llm = GoogleLLM(api_key="initial-key", model_name="gemini-2.0-flash-exp")
    google_llm.set_api_key("new-test-key")
    assert google_llm.api_key == "new-test-key"


def test_set_model_name(mock_llm):
    """
    Tests changing the model name after initialization.
    """
    google_llm = GoogleLLM(api_key="some-key", model_name="gemini-2.0-flash-exp")
    google_llm.set_model_name("gemini-3.0-beta")
    assert google_llm.model_name == "gemini-3.0-beta"


def test_invoke_method(mock_llm):
    """
    Tests invoking the LLM with a list of messages.
    """
    google_llm = GoogleLLM(api_key="some-key", model_name="gemini-2.0-flash-exp")
    messages = [("system", "Hello, how are you?"), ("human", "I'm fine!")]
    response = google_llm.invoke(messages)

    # Verify that the mock_invoke was actually called with the correct arguments
    mock_llm["mock_invoke"].assert_called_once_with(messages)
    # Verify the response
    assert response == "Mocked AI Response"


def test_stream_method(mock_llm):
    """
    Tests the stream method to confirm it yields chunks from the model.
    """
    google_llm = GoogleLLM(api_key="some-key", model_name="gemini-2.0-flash-exp")
    messages = [("system", "Please summarize."), ("human", "This is a test document.")]

    chunks = list(google_llm.stream(messages))  # Convert the generator to a list
    # Ensure mock_stream is called once
    mock_llm["mock_stream"].assert_called_once_with(messages)

    # The mock returned ['Chunk1', 'Chunk2'] for demonstration
    assert chunks == ["Chunk1", "Chunk2"]
