-- 006: apaga a causa de falha que ficou grudada em documento que deu certo.
--
-- MOTIVACAO: o UPDATE de sucesso da ingestao gravava status='completed' sem
-- tocar em `meta`. Um documento que falhou, foi reprocessado e completou
-- continuava carregando o erro da tentativa anterior. Em producao sao 6
-- documentos `completed` com um "You have not yet added your payment method"
-- de uma recusa antiga da Voyage.
--
-- Isso nao aparecia na tela porque a causa nunca era projetada pela API. Agora
-- que passa a ser, o residuo viraria erro exibido em documento que funciona.
--
-- O conserto da origem esta em jobs/ingestao.py, no mesmo UPDATE de sucesso:
-- `meta = COALESCE(meta,'{}'::jsonb) - 'error'`. Esta migration so limpa o que
-- ficou para tras.

UPDATE documents
   SET meta = COALESCE(meta, '{}'::jsonb) - 'error'
 WHERE status = 'completed'
   AND meta ? 'error';
