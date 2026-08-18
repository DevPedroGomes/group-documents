-- 004: o aviso de divergencia entre fontes entra na trilha de decisao.
--
-- MOTIVACAO: citar a fonte nao diz nada quando o acervo tem o contrato de 2023
-- e o aditivo de 2025 falando da mesma clausula. O RAG recupera os dois, o
-- gerador escolhe um e responde com confianca; quem le nao descobre que existia
-- outra versao. Agora, quando os trechos que sobraram vem de dois ou mais
-- documentos, uma checagem barata pergunta se eles divergem, e o aviso fica
-- guardado junto da decisao que o produziu.
--
-- FORMATO: {"summary": "...", "sources": ["Contrato 2023", "Aditivo 2025"]}
-- Nulo quando nao houve divergencia OU quando a checagem nao rodou (menos de
-- dois documentos, feature desligada, ou erro na chamada). Os tres casos sao
-- "sem aviso" para quem le, e a diferenca entre eles esta no log.

ALTER TABLE decisions
    ADD COLUMN IF NOT EXISTS conflict JSONB;

-- Achar as perguntas em que o acervo se contradiz: e o relatorio que diz quais
-- documentos precisam ser revisados ou arquivados.
CREATE INDEX IF NOT EXISTS idx_decisions_conflict
    ON decisions (user_id, created_at DESC)
    WHERE conflict IS NOT NULL;
