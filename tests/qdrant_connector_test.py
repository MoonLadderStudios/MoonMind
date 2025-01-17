import os
import sys
from pathlib import Path

# Add the dags directory to the Python path
dags_dir = Path(__file__).parent.parent / "dags"
sys.path.append(str(dags_dir))

import pytest
from moon_ai.qdrant_connector import QdrantConnector


def test_list_collections():
    qdrant_host = os.getenv('QDRANT_HOST', '192.168.0.3')
    qdrant_port = int(os.getenv('QDRANT_PORT', '6333'))
    collection_name = os.getenv('QDRANT_COLLECTION', 'vectors')

    connector = QdrantConnector(qdrant_host=qdrant_host, qdrant_port=qdrant_port, collection_name=collection_name)

    try:
        # Call list_collections and print the result
        collections = connector.list_collections()
        print("Available collections:", collections)

        # Assert that collections were retrieved (or customize the assertion as needed)
        assert isinstance(collections, list), "Collections should be a list"
        assert len(collections) > 0, "No collections found in Qdrant"
    except Exception as e:
        pytest.fail(f"Failed to list collections: {e}")