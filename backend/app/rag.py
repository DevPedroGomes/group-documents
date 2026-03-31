import os
from openai import OpenAI

# Usar OpenAI Embeddings
# Requer OPENAI_API_KEY no .env
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
EMB_MODEL = os.getenv('EMB_MODEL', 'text-embedding-3-small')

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Gera embeddings usando OpenAI text-embedding-3-small (1536 dimensions)."""
    if not texts: return []
    try:
        response = client.embeddings.create(
            model=EMB_MODEL,
            input=texts
        )
        # Retorna lista de vetores
        return [item.embedding for item in response.data]
    except Exception as e:
        print(f"Error generating embeddings: {e}")
        return []
