-- 008: a busca por palavra-chave passa a indexar portugues sem acento e ingles,
-- e a consulta (vector_store.py) casa QUALQUER termo em vez de todos.
--
-- MOTIVACAO: o trigger da 001 indexava com 'english' e a consulta fazia AND de
-- todos os termos da pergunta. "qual", "o", "de", "para" viravam termos
-- obrigatorios e uma pergunta em portugues quase nunca casava nada; a busca
-- "hibrida" era na pratica so vetorial. Medido com
-- scripts/avaliar_busca_textual.py (acervo demo de 500 documentos, 46 perguntas
-- em portugues e 10 em ingles), a configuracao abaixo leva o portugues de 89%
-- de perguntas sem resultado para 0%, e o MRR de 0,11 para 0,99; o ingles, de
-- 70% sem resultado para 0% (MRR 0,30 para 1,00).
--
-- `unaccent` e contrib, disponivel na imagem pgvector/pgvector:pg16 e marcado
-- como extensao confiavel (o dono do banco pode criar).
CREATE EXTENSION IF NOT EXISTS unaccent;

-- As stopwords do portugues sao acentuadas ("até", "não"). Tirar o acento
-- antes da checagem faria "ate"/"nao" sobreviverem como termos e casarem com
-- quase tudo; por isso o dicionario de stopwords vem ANTES do unaccent.
-- Nao existe CREATE ... IF NOT EXISTS para dicionario e config de texto.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_dict WHERE dictname = 'portugues_stopwords') THEN
        CREATE TEXT SEARCH DICTIONARY portugues_stopwords
            (TEMPLATE = simple, STOPWORDS = portuguese, ACCEPT = false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'portugues_sem_acento') THEN
        CREATE TEXT SEARCH CONFIGURATION portugues_sem_acento (COPY = portuguese);
        ALTER TEXT SEARCH CONFIGURATION portugues_sem_acento
            ALTER MAPPING FOR hword, hword_part, word
            WITH portugues_stopwords, unaccent, portuguese_stem;
    END IF;
END $$;

-- Vetor duplo: o acervo e portugues, mas o app aceita documento em ingles (o
-- manual de exemplo e ingles). As configs aqui e TEXT_SEARCH_CONFIGS em
-- vector_store.py tem de ser as mesmas; ha teste de integracao cruzando.
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
DECLARE
    texto TEXT := COALESCE(NEW.content, '') || ' ' || COALESCE(NEW.enriched_content, '');
BEGIN
    NEW.search_vector := to_tsvector('portugues_sem_acento', texto)
                      || to_tsvector('english', texto);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Recalcula as linhas existentes pelo PROPRIO trigger: mencionar `content` no
-- SET dispara o BEFORE UPDATE OF content, entao nao ha uma segunda copia da
-- expressao para divergir da funcao acima.
UPDATE chunks SET content = content;
