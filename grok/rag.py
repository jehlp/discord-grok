from .config import RAG_RESULTS
from .clients import message_collection


def store_message(message_id: str, content: str, author: str, channel: str, timestamp: str):
    """Store a message in ChromaDB for RAG retrieval."""
    if not content or len(content.strip()) < 3:
        return

    try:
        message_collection.upsert(
            ids=[message_id],
            documents=[content],
            metadatas=[{
                "author": author,
                "channel": channel,
                "timestamp": timestamp,
            }]
        )
    except Exception as e:
        print(f"Failed to store message: {e}")


def retrieve_relevant_context(query: str, exclude_ids: list[str] = None, min_distance: float = 0.25) -> list[dict]:
    """Retrieve relevant past messages for context, filtering by distance threshold."""
    try:
        results = message_collection.query(
            query_texts=[query],
            n_results=RAG_RESULTS,
            include=["documents", "metadatas", "distances"],
        )

        context = []
        if results and results["documents"] and results["documents"][0]:
            distances = results.get("distances", [[]])[0]
            for i, doc in enumerate(results["documents"][0]):
                msg_id = results["ids"][0][i] if results["ids"] else None
                if exclude_ids and msg_id in exclude_ids:
                    continue
                # Filter out low-relevance matches (higher distance = less relevant)
                if distances and i < len(distances) and distances[i] > (1.0 - min_distance):
                    continue
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                context.append({
                    "content": doc,
                    "author": metadata.get("author", "Unknown"),
                    "channel": metadata.get("channel", "Unknown"),
                })
        return context
    except Exception as e:
        print(f"RAG retrieval failed: {e}")
        return []
