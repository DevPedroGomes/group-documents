-- ==========================================
-- COMPLETE DATABASE SCHEMA FOR HUB-AGNO (OpenAI Embeddings)
-- Execute este SQL no SQL Editor do Supabase para um setup limpo
-- ==========================================

-- 1. HABILITAR EXTENSÕES
-- ==========================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. TABELAS
-- ==========================================

-- Tabela de documentos
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL, -- Quem fez uplaod
    title TEXT NOT NULL,
    mime TEXT,
    storage_path TEXT NOT NULL,
    status TEXT DEFAULT 'pending', -- pending, processing, completed, failed
    meta JSONB DEFAULT '{}',
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tabela de chunks com embeddings vetoriais (OpenAI text-embedding-3-small = 1536)
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL -- OpenAI text-embedding-3-small
);

-- Tabela de threads (conversas)
CREATE TABLE IF NOT EXISTS threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tabela de mensagens
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    meta JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. ÍNDICES PARA PERFORMANCE
-- ==========================================

-- Índices para queries frequentes
CREATE INDEX IF NOT EXISTS chunks_user_id_idx ON chunks(user_id);
CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks(document_id);
CREATE INDEX IF NOT EXISTS documents_user_id_idx ON documents(user_id);
CREATE INDEX IF NOT EXISTS documents_uploaded_at_idx ON documents(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS documents_status_idx ON documents(status);
CREATE INDEX IF NOT EXISTS threads_user_id_idx ON threads(user_id);
CREATE INDEX IF NOT EXISTS messages_thread_id_idx ON messages(thread_id);
CREATE INDEX IF NOT EXISTS messages_created_at_idx ON messages(created_at);

-- Índice HNSW para busca vetorial rápida (OpenAI 1536d)
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- 4. ROW LEVEL SECURITY (RLS) - TEAM HUB MODE
-- ==========================================
-- Neste modo, todos os usuários autenticados vêm todos os documentos (Team Space).
-- Mas threads de chat continuam privadas.

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

-- Documents: Shared (Todos autenticados podem ver/inserir/deletar)
CREATE POLICY "Team view all" ON documents FOR SELECT TO authenticated USING (true);
CREATE POLICY "Team insert all" ON documents FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Team update all" ON documents FOR UPDATE TO authenticated USING (true);
CREATE POLICY "Team delete all" ON documents FOR DELETE TO authenticated USING (true);

-- Chunks: Shared
CREATE POLICY "Team view chunks" ON chunks FOR SELECT TO authenticated USING (true);
CREATE POLICY "Team insert chunks" ON chunks FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "Team update chunks" ON chunks FOR UPDATE TO authenticated USING (true);
CREATE POLICY "Team delete chunks" ON chunks FOR DELETE TO authenticated USING (true);

-- Threads & Messages: Private (Privacidade do usuário mantida no chat)
CREATE POLICY "Private threads view" ON threads FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Private threads insert" ON threads FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Private threads delete" ON threads FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Private msg view" ON messages FOR SELECT USING (EXISTS (SELECT 1 FROM threads WHERE id = thread_id AND user_id = auth.uid()));
CREATE POLICY "Private msg insert" ON messages FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM threads WHERE id = thread_id AND user_id = auth.uid()));


-- 5. FUNÇÕES AUXILIARES (RPC)
-- ==========================================

-- Função para busca semântica em todo o Team Hub
CREATE OR REPLACE FUNCTION search_similar_chunks(
    query_embedding VECTOR(1536), -- OpenAI text-embedding-3-small
    match_threshold FLOAT DEFAULT 0.4,
    match_count INT DEFAULT 8,
    filter_doc_ids UUID[] DEFAULT NULL -- Filtro opcional por documentos específicos
)
RETURNS TABLE (
    id UUID,
    document_id UUID,
    document_title TEXT,
    page INTEGER,
    text TEXT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.document_id,
        d.title AS document_title,
        c.page,
        c.text,
        1 - (c.embedding <=> query_embedding) AS similarity
    FROM chunks c
    JOIN documents d ON c.document_id = d.id
    WHERE 1 - (c.embedding <=> query_embedding) >= match_threshold
    AND (filter_doc_ids IS NULL OR c.document_id = ANY(filter_doc_ids))
    ORDER BY c.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ==========================================
-- 6. STORAGE BUCKET SETUP (executar no Dashboard ou via SQL)
-- ==========================================
-- Criar bucket 'docs' no Supabase Storage Dashboard com as seguintes políticas:
--
-- Bucket: docs (private)
--
-- Políticas RLS para Storage (Team Hub - todos autenticados podem acessar):
--
-- SELECT (download):
--   authenticated users can download
--   Policy: (bucket_id = 'docs') AND (auth.role() = 'authenticated')
--
-- INSERT (upload):
--   authenticated users can upload to their folder
--   Policy: (bucket_id = 'docs') AND (auth.role() = 'authenticated')
--
-- DELETE:
--   authenticated users can delete
--   Policy: (bucket_id = 'docs') AND (auth.role() = 'authenticated')
--
-- IMPORTANTE: Configure via Dashboard > Storage > Policies
-- ==========================================
