import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

# Optional: If you want to type-hint specifically for LangChain Document:
from langchain.schema import Document as LangChainDocument


class BaseDocument:
    """Standardized document format across all connectors"""
    def __init__(
        self,
        text: str,
        metadata: Dict[str, Any],
        id: Optional[str] = None,
        source: Optional[str] = None,
        timestamp: Optional[str] = None
    ):
        self.text = text
        self.metadata = {
            **metadata,
            "source": source or metadata.get("source", "unknown"),
            "ingestion_timestamp": timestamp or datetime.utcnow().isoformat(),
        }
        self.id = id

    def to_ndjson(self) -> str:
        """Convert document to NDJSON format"""
        return json.dumps({
            "text": self.text,
            "metadata": self.metadata,
            "id": self.id
        }) + "\n"

    @classmethod
    def from_llama_document(cls, doc, source: Optional[str] = None):
        """
        Convert a LlamaIndex Document into a BaseDocument.
        Retained here for backward compatibility with any LlamaIndex-based flows.
        """
        return cls(
            text=doc.text,
            metadata=doc.metadata or {},
            id=doc.id_ if hasattr(doc, 'id_') else None,
            source=source
        )

    @classmethod
    def from_langchain_document(cls, doc, source: Optional[str] = None, doc_id: Optional[str] = None):
        """
        Convert a LangChain Document (langchain.schema.Document) into a BaseDocument.
        :param doc: LangChain Document instance
        :param source: Overwrite or set the "source" field in metadata
        :param doc_id: Optionally specify an ID; if not provided, we'll look in doc.metadata
        """
        # A typical LangChain Document has "page_content" for text, and "metadata" for the rest.
        text = getattr(doc, "page_content", "")
        metadata = getattr(doc, "metadata", {}) or {}

        # If doc_id is not explicitly provided, try to pull from metadata["id"] or default to None.
        if not doc_id:
            doc_id = metadata.get("id")

        return cls(
            text=text,
            metadata=metadata,
            id=doc_id,
            source=source
        )

class BaseConnector(ABC):
    """Base class for all document connectors"""

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def stream_documents(self, **kwargs) -> Generator[BaseDocument, None, None]:
        """Stream documents from the source"""
        pass

    def index_documents(self, documents: List[BaseDocument], **kwargs):
        """
        Optional method for connectors that support document indexing.
        Vector stores should implement this.
        """
        raise NotImplementedError("This connector does not support document indexing")

    def query(self, query_string: str, **kwargs):
        """
        Optional method for connectors that support querying.
        Vector stores should implement this.
        """
        raise NotImplementedError("This connector does not support querying")

    def to_ndjson_stream(self, **kwargs) -> Generator[str, None, None]:
        """Convert document stream to NDJSON format"""
        for doc in self.stream_documents(**kwargs):
            yield doc.to_ndjson()

    def batch_documents(
        self,
        batch_size: int = 10,
        **kwargs
    ) -> Generator[List[BaseDocument], None, None]:
        """Batch documents from the stream"""
        batch = []
        count = 0

        for doc in self.stream_documents(**kwargs):
            batch.append(doc)
            count += 1
            # Log progress optionally
            self._log_progress(count)

            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def test_connection(self) -> tuple[bool, Optional[str]]:
        """Test connection to the source by attempting to retrieve one document."""
        try:
            next(self.stream_documents())
            return True, None
        except StopIteration:
            # If your connector doesn't always guarantee documents,
            # you may treat an empty source as a success or handle differently.
            return True, None
        except Exception as e:
            return False, str(e)

    def _log_progress(self, count: int, interval: int = 10):
        """Log progress at specified intervals"""
        if count % interval == 0:
            self.logger.info(f"Processed {count} documents")
