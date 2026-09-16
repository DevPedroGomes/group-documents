-- 006: a busca por palavra-chave deixa de assumir que o acervo e em ingles.
--
-- MOTIVACAO: o trigger da 001 usa to_tsvector('english', ...) e a consulta usa
-- plainto_tsquery('english', ...). O acervo nao e em ingles — o da demo e em
-- portugues, e o produto aceita documento em qualquer lingua (o agente de voz
-- inclusive troca de idioma junto com a pessoa). Com a configuracao errada o
-- Postgres aplica o stemmer ingles a texto portugues e remove as stop words
-- erradas: "documentos" nao casa com "documento", e "a", "de", "para" ficam
-- indexadas enquanto "the" e descartada. A perna vetorial disfarca o estrago,
-- entao isso degrada em silencio.
--
-- POR QUE `simple` E NAO `portuguese`: nao existe coluna de idioma, e um acervo
-- pode ter documento em varias linguas ao mesmo tempo — escolher UMA so troca
-- de lugar quem fica errado. `simple` nao faz stemming nenhum, e nao faz
-- stemming ERRADO, que e o que acontece hoje.
--
-- E o que se perde e menor do que parece: numa busca hibrida o papel do BM25 e
-- casar termo EXATO (nome proprio, numero de contrato, codigo, sigla), e para
-- isso stemming atrapalha em vez de ajudar. A semantica — "documentos" ~
-- "documento" — e trabalho da perna vetorial, que ja faz isso bem.
--
-- Quem tiver acervo monolingue e quiser stemming pode trocar 'simple' pela
-- configuracao do idioma AQUI e em app/services/vector_store.py. As duas tem
-- de andar juntas: indexar com uma e consultar com outra devolve vazio, e ha
-- teste prendendo isso.

CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector := to_tsvector(
        'simple',
        COALESCE(NEW.content, '') || ' ' || COALESCE(NEW.enriched_content, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Os chunks ja indexados carregam o tsvector antigo: sem recalcular, o acervo
-- existente continua inconsultavel pela nova configuracao. O UPDATE dispara o
-- trigger recem-criado.
UPDATE chunks SET content = content;
