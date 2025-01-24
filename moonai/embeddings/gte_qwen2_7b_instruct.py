import logging
from typing import List, Optional

import requests
from langchain.embeddings.base import Embeddings


class GTEQwen27BInstruct(Embeddings):
    """
    Example embedding class for gte-qwen2-7b-instruct that calls an LM Studio server
    with an OpenAI-like /v1/embeddings endpoint.
    """

    def __init__(
        self,
        endpoint: str = "http://10.5.0.2:1234",
        model_name: str = "tensorblock/gte-Qwen2-7B-instruct-GGUF/gte-Qwen2-7B-instruct-Q8_0.gguf",
        embed_dim: int = 3584,
        context_length: int = 4096,      # If LM Studio doesn't need this, you can remove it.
        evaluation_batch_size: int = 16, # For chunking embed_documents into smaller batches.
        logger: Optional[logging.Logger] = None,
    ):
        """
        :param endpoint: Base URL for LM Studio (without /v1/embeddings).
        :param model_name: Name of the model as recognized by LM Studio.
        :param embed_dim: The dimension of the embeddings returned by the server.
        :param context_length: Unused if LM Studio doesn't handle it, but kept for reference.
        :param evaluation_batch_size: Batch size for embedding requests.
        :param logger: Optional logger. If none is provided, a default logger is used.
        """
        self.endpoint = endpoint.rstrip("/")  # remove trailing slash if present
        self.model_name = model_name
        self.embed_dim = embed_dim
        self.context_length = context_length
        self.evaluation_batch_size = evaluation_batch_size

        self.logger = logger or logging.getLogger(__name__)
        self.logger.info(
            "Initializing GTEQwen27BInstruct with endpoint=%s, model_name=%s, "
            "embed_dim=%d, context_length=%d, evaluation_batch_size=%d",
            self.endpoint,
            self.model_name,
            self.embed_dim,
            self.context_length,
            self.evaluation_batch_size,
        )

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Return embeddings for a list of documents, split into batches if needed.
        """
        embeddings = []
        for i in range(0, len(texts), self.evaluation_batch_size):
            batch = texts[i : i + self.evaluation_batch_size]
            embeddings.extend(self._get_batch_embeddings(batch))
        return embeddings

    def embed_query(self, text: str) -> List[float]:
        """
        Return the embedding for a single query text.
        """
        return self.get_text_embedding(text)

    def get_text_embedding(self, text: str) -> List[float]:
        """
        Convenience method to embed a single text.
        """
        batch_embeddings = self._get_batch_embeddings([text])
        return batch_embeddings[0]

    def _get_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Calls LM Studio's /v1/embeddings endpoint (OpenAI-compatible).
        Expects the server to return JSON like:
            {
              "data": [
                {
                  "object": "embedding",
                  "embedding": [...float...],
                  "index": 0
                },
                ...
              ],
              "model": "gte-qwen2-7b-instruct",
              ...
            }
        """
        # Construct the final URL: <endpoint>/v1/embeddings
        url = f"{self.endpoint}/v1/embeddings"

        # OpenAI-like payload
        payload = {
            "model": self.model_name,
            "input": texts,
            # If you have a custom extension that supports context_length, you could add it here:
            # "context_length": self.context_length,
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            if "data" not in data or not isinstance(data["data"], list):
                raise ValueError("No 'data' array in the server's embedding response.")

            # Parse the embeddings from the "data" field
            embeddings = []
            for item in data["data"]:
                emb = item.get("embedding")
                if emb is None:
                    raise ValueError("No 'embedding' field in one of the items.")
                if len(emb) != self.embed_dim:
                    raise ValueError(
                        f"Expected embedding size {self.embed_dim}, but got {len(emb)}."
                    )
                embeddings.append(emb)

            return embeddings

        except Exception as e:
            self.logger.error(f"Error obtaining embeddings from LM Studio: {e}")
            raise
