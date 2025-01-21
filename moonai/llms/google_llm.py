import getpass
import os

from langchain_google_genai import ChatGoogleGenerativeAI


class GoogleLLM:
    """
    A class to setup and interact with Google's Gemini chat models using LangChain.
    """

    def __init__(self, api_key=None, model_name="gemini-2.0-flash-exp", **kwargs):
        """
        Initializes the GoogleLLM class.

        Args:
            api_key (str, optional): Your Google AI API key. If not provided, it will attempt to
                retrieve from the GOOGLE_API_KEY environment variable or prompt the user.
            model_name (str, optional): The name of the Gemini model to use.
            **kwargs: Additional keyword arguments to pass to the ChatGoogleGenerativeAI constructor.
        """

        if api_key is None:
            self.api_key = os.environ["GOOGLE_API_KEY"]
        else:
            self.api_key = api_key

        self.model_name = model_name

        self.llm = ChatGoogleGenerativeAI(model=self.model_name, **kwargs)

    def set_api_key(self, api_key):
        self.api_key = api_key

    def set_model_name(self, model_name):
        self.model_name = model_name

    def invoke(self, messages):
        """
        Invokes the chat model with a list of messages.

        Args:
            messages (list): A list of tuples, where each tuple is a message in the format
                ("role", "content"). The role should be either "system" or "human".

        Returns:
            langchain_core.messages.AIMessage: The AI's response message.
        """
        return self.llm.invoke(messages)

    def stream(self, messages):
        """
        Streams partial messages (AIMessageChunk objects) from the model as they become available.

        Example usage:
            messages = [
                ("system", "Translate the user sentence to French."),
                ("human", "I love programming."),
            ]

            for chunk in google_llm.stream(messages):
                print(chunk)

        Args:
            messages (list): A list of tuples in the same format as invoke().

        Yields:
            AIMessageChunk: The partial model response, chunk by chunk.
        """
        yield from self.llm.stream(messages)