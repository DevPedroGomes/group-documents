import os
from openai import OpenAI

# Usar OpenAI Embeddings
# Requer OPENAI_API_KEY no .env
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
EMB_MODEL = os.getenv('EMB_MODEL', 'text-embedding-3-small')

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Gera embeddings usando OpenAI text-embedding-3-small (1536 dimensions).
    Raises on failure to prevent silent data corruption."""
    if not texts:
        return []
    response = client.embeddings.create(
        model=EMB_MODEL,
        input=texts
    )
    vectors = [item.embedding for item in response.data]
    if len(vectors) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(vectors)}")
    return vectors
