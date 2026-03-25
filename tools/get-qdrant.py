import argparse
import sys

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse


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
        default="localhost",
        help="Qdrant host (default: localhost).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=6333,
        help="Qdrant port (default: 6333).",
    )
    parser.add_argument(
        "--dim",
        type=int,
        default=384,
        help="Vector dimension (default: 384).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of results to retrieve (default: 5).",
    )
    args = parser.parse_args()

    try:
        client = QdrantClient(host=args.host, port=args.port, check_compatibility=False)

        # Placeholder query vector
        query_vector = [0.0] * args.dim

        search_result = client.search(
            collection_name=args.collection_name,
            query_vector=query_vector,
            limit=args.limit,
            with_payload=True,
        )

        for result in search_result:
            print("Document ID:", result.id)
            print("Payload:", result.payload)
            print("Score:", result.score)
            print("-----")

    except UnexpectedResponse as exc:
        print(f"Error: Unexpected response from Qdrant: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: An unexpected error occurred: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
