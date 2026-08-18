-- 003: A decisao de recuperacao vira registro consultavel, nao so texto na tela.
--
-- MOTIVACAO: a cada resposta o pipeline decide bastante coisa (quais variantes
-- de query gerou, quais trechos recuperou e com que score, o que o grader
-- aprovou, se o rerank rodou, se caiu na rede de seguranca de baixa confianca,
-- se precisou de busca web) e nada disso sobrevivia ao fim do stream. O painel
-- de workflow mostra o caminho enquanto acontece e some quando a pagina recarrega.
--
-- Com a decisao persistida da para responder depois "por que ele respondeu
-- isso?", comparar respostas ao longo do tempo, e achar a pergunta que sempre
-- cai na rede de seguranca — que e o sinal de que falta documento no acervo.
--
-- ISOLAMENTO: `user_id` em toda linha, como no resto do schema. A leitura da
-- trilha filtra por usuario, nunca so por message_id.
--
-- RETENCAO: sem TTL aqui de proposito. A linha e pequena (JSONB de metadados,
-- nao o texto dos trechos) e o valor da trilha cresce com o historico.

CREATE TABLE IF NOT EXISTS decisions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL,
    thread_id    UUID REFERENCES threads(id)  ON DELETE CASCADE,
    -- A resposta que esta decisao produziu. Fica nulo quando a geracao falhou
    -- antes de produzir texto: a trilha do caminho percorrido continua valendo.
    message_id   UUID REFERENCES messages(id) ON DELETE CASCADE,

    question     TEXT NOT NULL,

    -- Metadados dos trechos, nao o conteudo: id do documento, titulo, pagina e
    -- score. O texto ja vive em `chunks` e duplicar aqui so incharia a tabela.
    retrieved    JSONB NOT NULL DEFAULT '[]'::jsonb,
    graded       JSONB NOT NULL DEFAULT '[]'::jsonb,

    considered   INTEGER NOT NULL DEFAULT 0,
    kept         INTEGER NOT NULL DEFAULT 0,

    -- Qual escala o score usa muda a leitura do numero: `cohere` e calibrado
    -- (0 a 1), `rrf` fica na casa de 0,01 a 0,03 e `tavily` e outro criterio.
    -- Sem isso um score 0,03 parece pessimo quando na verdade e o topo do RRF.
    score_scale     TEXT,
    reranked        BOOLEAN NOT NULL DEFAULT FALSE,
    low_confidence  BOOLEAN NOT NULL DEFAULT FALSE,
    web_used        BOOLEAN NOT NULL DEFAULT FALSE,
    answered        BOOLEAN NOT NULL DEFAULT FALSE,

    latency_ms   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Listagem da trilha do usuario, do mais recente para o mais antigo.
CREATE INDEX IF NOT EXISTS idx_decisions_user_created
    ON decisions (user_id, created_at DESC);

-- Abrir a trilha a partir de uma resposta especifica.
CREATE INDEX IF NOT EXISTS idx_decisions_message
    ON decisions (message_id);

-- Percorrer a conversa inteira em ordem.
CREATE INDEX IF NOT EXISTS idx_decisions_thread
    ON decisions (thread_id, created_at);
