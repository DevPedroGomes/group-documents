-- 003: 1024 (Voyage, API) -> 768 (jina-clip-v1, local).
--
-- POR QUE SAIR DA VOYAGE. Numa unica auditoria (2026-08-06) tres provedores
-- quebraram este app de tres formas diferentes: a Voyage por rate limit (3
-- requisicoes/minuto no plano sem cartao, contra 4 embeddings por pergunta —
-- toda pergunta INEDITA falhava), a Cohere aposentando `rerank-v3.5` e
-- servindo um sucessor com outra escala de score, e o Google retirando o
-- endpoint de preview do Gemini que a ingestao multimodal usava.
--
-- Dessas dependencias, embedding e a UNICA com lock-in de verdade: trocar de
-- modelo significa re-embedar o corpus inteiro. LLM e roteador (trocar modelo
-- e uma variavel de ambiente) e transcricao e um endpoint isolado. Entao e
-- esta que vale trazer para dentro de casa.
--
-- O QUE ENTRA. `jinaai/jina-clip-v1` rodando localmente via FastEmbed/ONNX —
-- o mesmo runtime que o voice_rag ja roda nesta VPS. Texto e imagem saem das
-- duas torres do MESMO modelo, entao continuam no mesmo espaco vetorial, que
-- e o que permite uma pergunta escrita recuperar uma figura. Apache 2.0 (o
-- jina-clip-v2 e CC-BY-NC e proibiria uso comercial). 768 dimensoes dos dois
-- lados.
--
-- Medido nesta maquina, 2 vCPU: consulta 34 ms, chunk 413 ms, imagem 385 ms,
-- 1,7 GB de RSS com as duas torres, 851 MB em disco. A consulta ficou MAIS
-- rapida que a chamada de rede que substituiu; a ingestao ficou mais lenta,
-- mas ela ja era dominada pelas chamadas de LLM do enriquecimento contextual.
--
-- SEGURANCA DO DADO: `chunks` e `documents` estao vazias no momento desta
-- migration (verificado: 0 linhas). Embeddings de 1024 dims nao poderiam ser
-- convertidos para 768 de qualquer forma — teriam que ser regerados a partir
-- do texto original, que continua em disco. Fazer a troca agora custa uma
-- migration; adiar custa re-embedar o corpus inteiro depois.

-- Indices HNSW sao vinculados ao tipo da coluna; caem antes do ALTER.
DROP INDEX IF EXISTS idx_chunks_embedding;

-- Vetores de 1024 dims sao irrecuperaveis na dimensao nova: o ALTER falharia.
DELETE FROM chunks WHERE embedding IS NOT NULL;

ALTER TABLE chunks
    ALTER COLUMN embedding TYPE vector(768);

CREATE INDEX idx_chunks_embedding ON chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- A tabela `semantic_cache` foi removida do codigo na auditoria (declarada e
-- jamais lida). Se ainda existir no banco, sai junto — manter uma tabela orfa
-- com coluna vetorial na dimensao errada so cria confusao na proxima migration.
DROP INDEX IF EXISTS semantic_cache_embedding_idx;
DROP TABLE IF EXISTS semantic_cache;

-- A funcao SQL declara a dimensao na assinatura; a de 1024 nao serve mais.
DROP FUNCTION IF EXISTS search_similar_chunks(vector, double precision, integer, uuid[]);

-- Documentos marcados como falha por dimensao incompativel voltam para a fila:
-- o arquivo original continua em disco e agora seria reprocessado em 768.
UPDATE documents
   SET status = 'pending',
       meta   = meta - 'error'
 WHERE status = 'failed'
   AND meta->>'error' LIKE '%dimensions%';
