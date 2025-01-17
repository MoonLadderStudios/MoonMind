import os
import sys

from moonai.connectors.qdrant_connector import QdrantConnector


def delete_collection(collection_name):
    try:
        qdrant_host = os.getenv("QDRANT_HOST")
        qdrant_port = os.getenv("QDRANT_PORT")
        qdrantClient = QdrantConnector(
            qdrant_host=qdrant_host,
            qdrant_port=qdrant_port,
            collection_name_prefix='kobi'
        )
        qdrantClient.delete_collection(collection_name)
        print(f"Collection {collection_name} deleted successfully")
    except Exception as e:
        print(f"Error deleting collection {collection_name}: {e}")

if __name__ == "__main__":
    collection_name = sys.argv[1]
    delete_collection(collection_name)
