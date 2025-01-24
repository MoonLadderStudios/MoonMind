import os
import uuid

from langchain_google_genai import ChatGoogleGenerativeAI
from langsmith import Client  # for optional feedback logging
from langsmith import traceable


class GoogleLLM:
    """
    A class to setup and interact with Google's Gemini chat models using LangChain.
    """

    def __init__(self, api_key=None, model_name="gemini-2.0-flash-exp", **kwargs):
        """
        Initializes the GoogleLLM class.
        """
        # Grab your API key
        if api_key is None:
            self.api_key = os.environ["GOOGLE_API_KEY"]
        else:
            self.api_key = api_key

        # Save model name
        self.model_name = model_name

        # Initialize the underlying LLM
        self.llm = ChatGoogleGenerativeAI(model=self.model_name, **kwargs)

    def set_api_key(self, api_key):
        self.api_key = api_key

    def set_model_name(self, model_name):
        self.model_name = model_name

    @traceable(metadata={"llm_provider": "GoogleGenAI"})
    def invoke(self, messages, **kwargs):
        """
        Invokes the chat model with a list of messages.

        Args:
            messages (list): A list of tuples [("role", "content"), ...]
            **kwargs: You can pass in 'langsmith_extra' dict,
                      which can contain 'metadata' or 'run_id'.

        Returns:
            AIMessage: The AI's response message.
        """
        # If you want to handle or pass along run_id or metadata:
        langsmith_extra = kwargs.get("langsmith_extra", {})
        run_id = langsmith_extra.get("run_id")
        custom_metadata = langsmith_extra.get("metadata", {})

        # If you'd like, you can print them or do other logic here
        if run_id:
            print(f"Running LLM call with run_id: {run_id}")
        if custom_metadata:
            print(f"Attaching custom metadata: {custom_metadata}")

        return self.llm.invoke(messages)

    # You could also trace your streaming calls
    @traceable(metadata={"llm_provider": "GoogleGenAI", "streaming": True})
    def stream(self, messages):
        """
        Streams partial messages (AIMessageChunk objects) from the model as they become available.
        """
        yield from self.llm.stream(messages)
