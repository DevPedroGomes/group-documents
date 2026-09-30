-- 008: a busca por palavra-chave passa a indexar portugues (sem acento, quando
-- a extensao existir) e ingles, e a consulta (vector_store.py) casa QUALQUER
-- termo em vez de todos.
--
-- MOTIVACAO: o trigger da 001 indexava com 'english' e a consulta fazia AND de
-- todos os termos. "qual", "o", "de", "para" viravam termos obrigatorios e
-- pergunta em portugues quase nunca casava nada; a busca "hibrida" era na
-- pratica so vetorial. Numeros, e o que eles NAO mostram, no comentario de
-- TEXT_SEARCH_CONFIGS em vector_store.py (scripts/avaliar_busca_textual.py).

-- `unaccent` e contrib e extensao confiavel, mas o Postgres de producao e
-- externo e o papel pode nao poder cria-la. Sem ela a busca segue, so sem
-- tirar acento; falhar aqui derrubaria o boot, porque migrate.py e fatal.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS unaccent;
EXCEPTION WHEN insufficient_privilege OR undefined_file OR feature_not_supported THEN
    RAISE WARNING '008: unaccent indisponivel (%); a busca textual segue sem tirar acento. Para ativar: %',
        SQLERRM,
        'instalar a extensao unaccent; ALTER TEXT SEARCH CONFIGURATION busca_portugues '
        || 'ALTER MAPPING FOR word, hword, hword_part WITH portugues_stopwords, '
        || 'ingles_stopwords, <schema da extensao>.unaccent, portuguese_stem; '
        || 'reindexar com UPDATE chunks SET content = content.';
END $$;

-- Cada metade descarta as stopwords das DUAS linguas antes do stemmer. Com OR,
-- "de"/"o"/"qual" (que o stemmer ingles nao conhece) e "the"/"is" (que o
-- portugues nao conhece) virariam termos e casariam trecho sem relacao. As
-- stopwords portuguesas sao acentuadas ("não", "até"): o descarte vem ANTES do
-- unaccent. Config ja criada nao e refeita; instalar `unaccent` depois exige
-- refazer o mapeamento de busca_portugues e reindexar.
-- O dicionario vai qualificado pelo schema da extensao: em Postgres gerenciado
-- ela costuma morar fora do search_path (ex.: `extensions`), e o nome solto
-- nao resolveria.
DO $$
DECLARE
    schema_unaccent text := (
        SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
         WHERE e.extname = 'unaccent'
    );
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_dict WHERE dictname = 'portugues_stopwords') THEN
        CREATE TEXT SEARCH DICTIONARY portugues_stopwords
            (TEMPLATE = simple, STOPWORDS = portuguese, ACCEPT = false);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_ts_dict WHERE dictname = 'ingles_stopwords') THEN
        CREATE TEXT SEARCH DICTIONARY ingles_stopwords
            (TEMPLATE = simple, STOPWORDS = english, ACCEPT = false);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'busca_portugues') THEN
        CREATE TEXT SEARCH CONFIGURATION busca_portugues (COPY = portuguese);
        ALTER TEXT SEARCH CONFIGURATION busca_portugues
            ALTER MAPPING FOR asciiword, asciihword, hword_asciipart
            WITH portugues_stopwords, ingles_stopwords, portuguese_stem;
        IF schema_unaccent IS NOT NULL THEN
            EXECUTE format(
                'ALTER TEXT SEARCH CONFIGURATION busca_portugues '
                'ALTER MAPPING FOR word, hword, hword_part '
                'WITH portugues_stopwords, ingles_stopwords, %I.unaccent, portuguese_stem',
                schema_unaccent);
        ELSE
            ALTER TEXT SEARCH CONFIGURATION busca_portugues
                ALTER MAPPING FOR word, hword, hword_part
                WITH portugues_stopwords, ingles_stopwords, portuguese_stem;
        END IF;
    END IF;

    -- Metade inglesa so com palavra ASCII: palavra acentuada e portuguesa, ja
    -- esta na outra metade, e aqui so repetiria o termo com o stemmer errado.
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'busca_ingles') THEN
        CREATE TEXT SEARCH CONFIGURATION busca_ingles (COPY = english);
        ALTER TEXT SEARCH CONFIGURATION busca_ingles
            ALTER MAPPING FOR asciiword, asciihword, hword_asciipart
            WITH portugues_stopwords, english_stem;
        ALTER TEXT SEARCH CONFIGURATION busca_ingles DROP MAPPING FOR word, hword, hword_part;
    END IF;
END $$;

-- As configs aqui e TEXT_SEARCH_CONFIGS em vector_store.py tem de ser as
-- mesmas; ha teste de integracao cruzando.
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
DECLARE
    texto TEXT := COALESCE(NEW.content, '') || ' ' || COALESCE(NEW.enriched_content, '');
BEGIN
    NEW.search_vector := to_tsvector('busca_portugues', texto)
                      || to_tsvector('busca_ingles', texto);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Recalcula as linhas existentes pelo PROPRIO trigger: mencionar `content` no
-- SET dispara o BEFORE UPDATE OF content, entao nao ha uma segunda copia da
-- expressao para divergir da funcao acima.
UPDATE chunks SET content = content;
