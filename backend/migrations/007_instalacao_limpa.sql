-- 007: fecha a distancia entre o schema das migrations e o que o codigo usa.
--
-- MOTIVACAO: producao nasceu de um `init.sql` que ja tinha `users.is_active` e
-- `threads.updated_at`; as migrations 001-006 nunca criaram essas colunas. Num
-- banco criado so por migrations, register/login/me e a lista de threads
-- quebravam com UndefinedColumn. Tudo aqui e idempotente: em producao vira
-- no-op, em banco novo completa o schema.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

-- Adicionada nullable, preenchida e so entao travada em NOT NULL, para nao
-- depender de o default cobrir linhas antigas em qualquer versao do Postgres.
ALTER TABLE threads
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
UPDATE threads SET updated_at = created_at WHERE updated_at IS NULL;
UPDATE threads SET updated_at = now() WHERE updated_at IS NULL;
ALTER TABLE threads ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE threads ALTER COLUMN updated_at SET NOT NULL;

-- Data em que o documento vale (emissao), distinta de `uploaded_at`. O recorte
-- `as_of` usa esta, com `uploaded_at` como reserva.
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS effective_date DATE;

-- So converte quando `emitido_em` e uma data ISO valida. Cast direto (ou
-- `to_date`) para dia inexistente, como 2024-02-31, levanta erro e derrubaria a
-- migration inteira; por isso o formato e o ultimo dia do mes sao conferidos em
-- CASEs aninhados, que (ao contrario de AND) garantem a ordem de avaliacao.
UPDATE documents
   SET effective_date = CASE
        WHEN meta->>'emitido_em' ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'
        THEN CASE
            WHEN substr(meta->>'emitido_em', 9, 2)::int <= EXTRACT(DAY FROM (
                     to_date(substr(meta->>'emitido_em', 1, 7) || '-01', 'YYYY-MM-DD')
                     + INTERVAL '1 month' - INTERVAL '1 day'))::int
            THEN to_date(meta->>'emitido_em', 'YYYY-MM-DD')
        END
   END
 WHERE effective_date IS NULL
   AND jsonb_typeof(meta->'emitido_em') = 'string';

CREATE INDEX IF NOT EXISTS idx_documents_user_effective_date
    ON documents (user_id, effective_date);

-- Consultas de busca que produziram a resposta (variantes geradas pelo pipeline).
ALTER TABLE decisions
    ADD COLUMN IF NOT EXISTS queries JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Cache semantico nunca foi implementado (ver settings.py); a tabela so ocupava
-- espaco e carregava um indice vetorial.
DROP TABLE IF EXISTS semantic_cache;
