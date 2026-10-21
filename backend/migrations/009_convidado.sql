-- 009: sessao de convidado.
--
-- MOTIVACAO: para experimentar o BrainHub era preciso criar conta e subir os
-- proprios arquivos. A sessao de convidado e um usuario temporario que le o
-- acervo de demonstracao (so leitura) e conversa com ele; as threads, mensagens
-- e trilhas dele ficam no proprio id, isoladas das de outro convidado.
--
-- `expires_at` so vale para convidado: a API recusa o token depois dele e o
-- cron `limpar_convidados` do worker apaga a linha com as conversas. Conta
-- normal fica com `is_guest = false` e `expires_at` nulo.
--
-- Idempotente, como as demais: `ADD COLUMN IF NOT EXISTS` e o indice com
-- `IF NOT EXISTS`. O DEFAULT constante nao reescreve a tabela.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_guest BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL;

-- A faxina procura so convidados vencidos: o indice parcial fica do tamanho
-- das sessoes abertas, nao da tabela de usuarios.
CREATE INDEX IF NOT EXISTS users_convidado_expira ON users (expires_at) WHERE is_guest;
