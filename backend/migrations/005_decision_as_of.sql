-- 005: o recorte temporal usado na resposta entra na trilha.
--
-- MOTIVACAO: com `as_of` a mesma pergunta produz respostas diferentes conforme
-- a data escolhida, e isso e a feature, nao um bug. Sem registrar qual recorte
-- foi usado, duas trilhas da mesma pergunta ficam inexplicavelmente
-- divergentes para quem for auditar depois.
--
-- Nulo significa "com o acervo inteiro, como ele esta hoje", que e o padrao.

ALTER TABLE decisions
    ADD COLUMN IF NOT EXISTS as_of TIMESTAMPTZ;
