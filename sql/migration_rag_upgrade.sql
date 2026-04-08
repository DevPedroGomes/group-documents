-- ==========================================
-- RAG UPGRADE MIGRATION
-- Run in Supabase SQL Editor after schema_complete.sql
-- ==========================================

-- 1. Add full-text search vector to chunks (for hybrid search)
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector
  GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

CREATE INDEX IF NOT EXISTS chunks_search_vector_idx ON chunks USING gin(search_vector);

-- 2. Tune HNSW index with explicit parameters
DROP INDEX IF EXISTS chunks_embedding_idx;
CREATE INDEX chunks_embedding_idx ON chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 3. Add composite index for filtered searches
CREATE INDEX IF NOT EXISTS chunks_doc_chunk_idx ON chunks (document_id, chunk_index);

-- 4. Add summary and chunk_count to documents
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_count integer DEFAULT 0;

-- 5. Create semantic cache table
CREATE TABLE IF NOT EXISTS semantic_cache (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query_hash text NOT NULL,
  query_embedding vector(1536),
  query_text text NOT NULL,
  response_text text NOT NULL,
  citations jsonb,
  document_ids uuid[],
  created_at timestamptz DEFAULT now(),
  expires_at timestamptz DEFAULT now() + interval '1 hour'
);

CREATE INDEX IF NOT EXISTS semantic_cache_embedding_idx
  ON semantic_cache USING hnsw (query_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS semantic_cache_expires_idx
  ON semantic_cache (expires_at);

-- 6. Add trigger for threads.updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS threads_updated_at ON threads;
CREATE TRIGGER threads_updated_at
  BEFORE UPDATE ON threads FOR EACH ROW EXECUTE FUNCTION update_updated_at();
