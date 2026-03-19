import argparse

from qdrant_client import QdrantClient


def main():
    parser = argparse.ArgumentParser(
        description="Query Qdrant collection to inspect payload data."
    )
    parser.add_argument(
        "collection_name",
        nargs="?",
        default="kobi",
        help="The name of the collection to query (default: kobi).",
    )
    parser.add_argument(
        "--host",
        default="192.168.0.3",
        help="Qdrant host (default: 192.168.0.3).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=6333,
        help="Qdrant port (default: 6333).",
    )
    args = parser.parse_args()

    client = QdrantClient(host=args.host, port=args.port)

    # Placeholder query vector with 384 dimensions
    query_vector = [0.0] * 384

    search_result = client.search(
        collection_name=args.collection_name,
        query_vector=query_vector,
        limit=5,
        with_payload=True,
    )

    for result in search_result:
        print("Document ID:", result.id)
        print("Payload:", result.payload)
        print("Score:", result.score)
        print("-----")


if __name__ == "__main__":
    main()
