# Import QdrantClient
from qdrant_client import QdrantClient

# Initialize the Qdrant client (adjust host and port as needed)
client = QdrantClient(host="192.168.0.3", port=6333)

# if an arg is passed, use it as the collection name, otherwise use "kobi"
import sys

if len(sys.argv) > 1:
    collection_name = sys.argv[1]
else:
    collection_name = "kobi"
query_vector = [0.0] * 384  # Placeholder query vector with the same dimensionality as your indexed vectors

# Query the collection to inspect payload data
search_result = client.search(
    collection_name=collection_name,
    query_vector=query_vector,
    limit=5,  # Limit the number of documents retrieved for inspection
    with_payload=True  # Retrieve payload data along with vectors
)

# Print the results to inspect the payload
for result in search_result:
    print("Document ID:", result.id)
    print("Payload:", result.payload)  # Print the payload to see stored metadata/content
    print("Score:", result.score)
    print("-----")
