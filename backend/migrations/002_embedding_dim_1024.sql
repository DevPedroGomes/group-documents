-- 002: Corrige a dimensao dos vetores de 1536 (OpenAI) para 1024 (Voyage).
--
-- BUG QUE ISTO CORRIGE: o codigo migrou pra Voyage (`voyage-3-large`, 1024
-- dims) mas as colunas continuaram `vector(1536)`, herdadas do
-- `text-embedding-3-small`. Resultado: TODA ingestao morria no INSERT com
--     ValueError: expected 1536 dimensions, not 1024
-- e a tabela `chunks` ficou vazia desde sempre — o RAG nunca teve o que
-- recuperar. Confirmado em producao em 2026-07-25.
--
-- Bug irmao, corrigido no codigo junto com esta migration: `embed_query` usava
-- `voyage-3-lite` (512 dims) contra documentos de `voyage-3-large` (1024).
-- Alinhar so a coluna NAO bastaria — a busca continuaria quebrando com
-- "different vector dimensions 1024 and 512". Modelos diferentes produzem
-- espacos vetoriais nao-comparaveis; no Voyage quem diferencia os dois lados e
-- o `input_type`, nao o modelo.
--
-- SEGURANCA DO DADO: nao ha perda. `chunks` esta vazia (nenhuma ingestao
-- jamais teve sucesso) e `semantic_cache` e cache descartavel com TTL de 1h.
-- Embeddings de 1536 dims nao poderiam ser convertidos para 1024 de qualquer
-- forma — teriam que ser regerados a partir do texto.

-- Cache antigo e inutilizavel na dimensao nova: descartar antes do ALTER.
TRUNCATE TABLE semantic_cache;

-- Indices HNSW sao vinculados ao tipo da coluna; precisam cair antes do ALTER.
DROP INDEX IF EXISTS idx_chunks_embedding;
DROP INDEX IF EXISTS semantic_cache_embedding_idx;

-- Qualquer chunk pre-existente (nao deve haver) teria embedding na dimensao
-- errada e seria lixo irrecuperavel — o USING abaixo falharia. Zeramos para
-- que a migration seja deterministica.
DELETE FROM chunks WHERE embedding IS NOT NULL;

ALTER TABLE chunks
    ALTER COLUMN embedding TYPE vector(1024);

ALTER TABLE semantic_cache
    ALTER COLUMN query_embedding TYPE vector(1024);

CREATE INDEX idx_chunks_embedding ON chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX semantic_cache_embedding_idx ON semantic_cache
    USING hnsw (query_embedding vector_cosine_ops);

-- A funcao SQL declara a dimensao na assinatura; recriar com 1024.
-- Assinatura muda => o DROP precisa ser explicito.
DROP FUNCTION IF EXISTS search_similar_chunks(vector, double precision, integer, uuid[]);

-- Documentos ja marcados como falha por causa DESTE bug voltam para a fila.
-- Sao reprocessaveis: o arquivo original continua em disco.
UPDATE documents
   SET status = 'pending',
       meta   = meta - 'error'
 WHERE status = 'failed'
   AND meta->>'error' LIKE '%expected 1536 dimensions%';
