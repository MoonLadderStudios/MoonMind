import json
from datetime import datetime, timedelta
from typing import Generator, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from langchain.schema import Document as LangChainDocument
from moonmind.connectors.base_connector import BaseConnector, BaseDocument

#
# Tests for BaseDocument
#

def test_base_document_creation():
    doc = BaseDocument(
        text="Hello World",
        metadata={"foo": "bar"},
        id="doc-123",
        source="test_source",
        timestamp="2024-01-01T00:00:00"
    )

    assert doc.text == "Hello World"
    assert doc.metadata["foo"] == "bar"
    assert doc.metadata["source"] == "test_source"
    assert doc.metadata["ingestion_timestamp"] == "2024-01-01T00:00:00"
    assert doc.id == "doc-123"

def test_base_document_default_source_and_timestamp():
    doc = BaseDocument(
        text="Sample text",
        metadata={}
    )

    # If source is not provided, default is 'unknown'
    assert doc.metadata["source"] == "unknown"
    # Ingestion timestamp should be auto-generated
    assert "ingestion_timestamp" in doc.metadata

def test_base_document_to_ndjson():
    doc = BaseDocument(
        text="NDJSON text",
        metadata={"key": "value"},
        id="ndjson-1"
    )
    ndjson_str = doc.to_ndjson()
    parsed = json.loads(ndjson_str.strip())

    assert parsed["text"] == "NDJSON text"
    assert parsed["metadata"]["key"] == "value"
    assert parsed["id"] == "ndjson-1"

def test_base_document_from_llama_document():
    class MockLlamaDoc:
        text = "Llama doc text"
        metadata = {"llama_key": "llama_value"}
        id_ = "llama-doc-1"

    llama_doc = MockLlamaDoc()
    base_doc = BaseDocument.from_llama_document(llama_doc, source="llama_source")

    assert base_doc.text == "Llama doc text"
    assert base_doc.metadata["llama_key"] == "llama_value"
    assert base_doc.metadata["source"] == "llama_source"
    assert base_doc.id == "llama-doc-1"

def test_base_document_from_langchain_document():
    # Typical langchain Document has "page_content" and "metadata"
    lc_doc = LangChainDocument(
        page_content="LangChain doc content",
        metadata={"langchain_key": "langchain_value", "id": "lc-doc-99"}
    )
    base_doc = BaseDocument.from_langchain_document(
        lc_doc,
        source="lc_source",
        doc_id=None
    )
    assert base_doc.text == "LangChain doc content"
    assert base_doc.metadata["langchain_key"] == "langchain_value"
    assert base_doc.metadata["source"] == "lc_source"
    assert base_doc.id == "lc-doc-99"


#
# Tests for BaseConnector
#

class MockConnector(BaseConnector):
    """A mock connector implementing stream_documents for testing."""
    def stream_documents(self, **kwargs) -> Generator[BaseDocument, None, None]:
        for i in range(kwargs.get("num_docs", 0)):
            yield BaseDocument(
                text=f"Document {i}",
                metadata={"index": i},
                id=str(i)
            )


def test_mock_connector_stream_documents():
    connector = MockConnector()
    docs = list(connector.stream_documents(num_docs=3))
    assert len(docs) == 3
    assert docs[0].text == "Document 0"
    assert docs[1].id == "1"

def test_mock_connector_ndjson_stream():
    connector = MockConnector()
    ndjson_gen = connector.to_ndjson_stream(num_docs=2)
    ndjson_list = list(ndjson_gen)

    assert len(ndjson_list) == 2
    parsed_0 = json.loads(ndjson_list[0].strip())
    parsed_1 = json.loads(ndjson_list[1].strip())

    assert parsed_0["text"] == "Document 0"
    assert parsed_1["id"] == "1"

def test_mock_connector_batch_documents():
    connector = MockConnector()
    batches = list(connector.batch_documents(batch_size=2, num_docs=5))

    # We expect 3 batches: 2 + 2 + 1
    assert len(batches) == 3
    assert len(batches[0]) == 2
    assert len(batches[1]) == 2
    assert len(batches[2]) == 1

def test_mock_connector_test_connection_success():
    connector = MockConnector()
    is_connected, err = connector.test_connection()
    assert is_connected is True
    assert err is None

def test_mock_connector_test_connection_empty():
    connector = MockConnector()
    # If num_docs=0, stream_documents yields nothing -> StopIteration is normal
    with patch.object(connector, 'stream_documents', return_value=iter([])):
        is_connected, err = connector.test_connection()
    # By default we treat empty as success
    assert is_connected is True
    assert err is None

def test_mock_connector_test_connection_exception():
    connector = MockConnector()
    with patch.object(connector, 'stream_documents', side_effect=Exception("Test error")):
        is_connected, err = connector.test_connection()
    assert is_connected is False
    assert err == "Test error"

def test_mock_connector_index_documents_not_implemented():
    connector = MockConnector()
    with pytest.raises(NotImplementedError):
        connector.index_documents([])

def test_mock_connector_query_not_implemented():
    connector = MockConnector()
    with pytest.raises(NotImplementedError):
        connector.query("test query")

def test_mock_connector_log_progress(caplog):
    connector = MockConnector()
    with caplog.at_level("INFO"):
        list(connector.batch_documents(batch_size=2, num_docs=10))
    # The connector logs progress every 10 documents by default
    # We expect exactly one log after 10 documents
    assert "Processed 10 documents" in caplog.text
