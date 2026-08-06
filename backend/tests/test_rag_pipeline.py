"""Testes que prendem os defeitos encontrados na auditoria de 2026-08-06.

Todos aqui sao regressoes reais que estavam em PRODUCAO, nao casos hipoteticos.
Nenhum toca a rede: o que cada um verifica e a logica que quebrou, e a logica
quebrou em lugares onde uma chamada de API nao teria ajudado a perceber.

O criterio para um teste entrar aqui: se ele tivesse existido, o bug nao teria
chegado ao ar.
"""

import ast
import inspect
from pathlib import Path

import pytest

from app.core.rag.grader import grade_documents


BACKEND = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. O limiar de relevancia contra a escala do score
#
# `relevance_score` chega ao grader vindo de duas origens: relevancia calibrada
# 0..1 quando o reranker da Cohere roda, e score de RRF quando nao roda. O
# limiar era 0.7 absoluto aplicado aos dois. Com rrf_k=60 o maximo teorico de
# um chunk e 2/(60+1) = 0,0328, entao 100% dos documentos eram descartados em
# toda pergunta, sempre.
# ---------------------------------------------------------------------------

def _doc(score: float, escala: str | None = None, ident: str = "c1") -> dict:
    d = {"id": ident, "relevance_score": score, "snippet": "texto", "document_id": "d1",
         "document_title": "t", "page": 1}
    if escala is not None:
        d["score_scale"] = escala
    return d


def test_score_de_rrf_nao_e_medido_contra_limiar_absoluto():
    """O bug exato: RRF no maximo teorico ainda seria descartado por 0.7."""
    maximo_teorico_rrf = 2 / 61  # 0,0328: rank 1 nas DUAS listas
    docs = [_doc(maximo_teorico_rrf, "rrf", "a"), _doc(0.016, "rrf", "b")]

    mantidos, _ = grade_documents(docs)

    assert len(mantidos) == 2, (
        "score de RRF foi filtrado por um limiar de escala Cohere — "
        "era isto que zerava a recuperacao em toda pergunta"
    )


def test_rrf_saudavel_nao_dispara_busca_web():
    """needs_web_search ficava permanentemente ligado, pagando um rewrite por
    pergunta que nunca era usado."""
    docs = [_doc(0.03, "rrf", "a"), _doc(0.02, "rrf", "b")]
    _, precisa_web = grade_documents(docs)
    assert precisa_web is False


def test_escala_ausente_e_tratada_como_nao_calibrada():
    """Default seguro: sem etiqueta, NAO aplicar limiar absoluto."""
    docs = [_doc(0.02), _doc(0.01)]
    mantidos, _ = grade_documents(docs)
    assert len(mantidos) == 2


def test_limiar_absoluto_vale_quando_o_score_e_calibrado():
    """Com Cohere o limiar volta a fazer sentido e deve mesmo filtrar."""
    docs = [_doc(0.91, "cohere", "a"), _doc(0.80, "cohere", "b"), _doc(0.30, "cohere", "c")]
    mantidos, _ = grade_documents(docs)
    assert [d["id"] for d in mantidos] == ["a", "b"]


def test_cohere_com_tudo_abaixo_do_limiar_nao_deixa_o_gerador_sem_nada():
    docs = [_doc(0.10, "cohere", "a"), _doc(0.05, "cohere", "b")]
    mantidos, precisa_web = grade_documents(docs)
    assert mantidos, "gerador ficaria sem contexto nenhum"
    assert precisa_web is True


def test_lote_vazio_pede_busca_web():
    mantidos, precisa_web = grade_documents([])
    assert mantidos == []
    assert precisa_web is True


# ---------------------------------------------------------------------------
# 2. O texto que chega ao gerador
#
# `hybrid_search` devolvia left(COALESCE(enriched_content, content), 500) e esse
# campo vai direto para o prompt. Chunks tem 500 TOKENS (~2000 chars), entao o
# modelo recebia menos de um terco, cortado no meio de uma frase. Com
# enriquecimento o prefixo gerado pela IA comia ~300 dos 500 caracteres.
# ---------------------------------------------------------------------------

def _sql_do_hybrid_search() -> str:
    return (BACKEND / "app/services/vector_store.py").read_text()


def test_snippet_nao_e_truncado_em_500_caracteres():
    sql = _sql_do_hybrid_search()
    assert "), 500) as snippet" not in sql, (
        "snippet voltou a ser cortado em 500 chars — o gerador recebe menos de "
        "um terco do chunk"
    )


def test_snippet_vem_do_chunk_cru_e_nao_do_enriquecido():
    """O contexto escrito na ingestao serve ao EMBEDDING. Manda-lo ao gerador
    faz o modelo ler o resumo de outro modelo em vez do documento."""
    sql = _sql_do_hybrid_search()
    assert "left(c.content," in sql
    assert "COALESCE(c.enriched_content" not in sql


def test_snippet_cabe_um_chunk_inteiro():
    """chunk_size=500 tokens ~ 2000 caracteres. O teto tem de ser maior."""
    from app.config.settings import Settings

    sql = _sql_do_hybrid_search()
    import re

    limites = {int(m) for m in re.findall(r"left\(c\.content,\s*(\d+)\)", sql)}
    assert limites, "nao achei o teto do snippet no SQL"
    chars_por_token_aprox = 4
    minimo = Settings.model_fields["chunk_size"].default * chars_por_token_aprox
    assert min(limites) >= minimo, f"teto {min(limites)} menor que um chunk (~{minimo} chars)"


# ---------------------------------------------------------------------------
# 3. Uma requisicao de embedding por pergunta, nao uma por variante
#
# O retriever pedia o embedding de cada variante do multi-query numa chamada
# separada: 4 requisicoes por pergunta contra o limite de 3/minuto do plano
# Voyage sem meio de pagamento. Toda pergunta INEDITA falhava; repetida
# funcionava, porque vinha do cache — o que fazia parecer intermitente.
# ---------------------------------------------------------------------------

def test_retriever_pede_embeddings_em_lote():
    from app.core.rag import retriever

    fonte = inspect.getsource(retriever.retrieve_documents)
    assert "get_query_embeddings(" in fonte, "voltou a embedar uma query por vez"


def test_embed_queries_faz_uma_unica_chamada_para_n_textos(monkeypatch):
    from app.services import embedding

    chamadas = []

    class ClienteFalso:
        def multimodal_embed(self, inputs, model, input_type):
            chamadas.append(list(inputs))
            return type("R", (), {"embeddings": [[0.0] * 1024 for _ in inputs]})()

    monkeypatch.setattr(embedding, "_get_client", lambda: ClienteFalso())
    vetores = embedding.embed_queries(["a", "b", "c", "d"])

    assert len(chamadas) == 1, f"{len(chamadas)} requisicoes para 4 queries"
    assert len(vetores) == 4


def test_cache_de_embedding_so_pede_o_que_falta(monkeypatch):
    from app.services import embedding_cache

    cache = {"ja tenho": [1.0] * 1024}
    pedidos = []

    monkeypatch.setattr(embedding_cache, "get_cached_embedding", lambda q: cache.get(q))
    monkeypatch.setattr(embedding_cache, "cache_embedding", lambda q, v: cache.setdefault(q, v))

    import app.services.embedding as emb

    def falso_embed_queries(textos):
        pedidos.append(list(textos))
        return [[2.0] * 1024 for _ in textos]

    monkeypatch.setattr(emb, "embed_queries", falso_embed_queries)

    saida = embedding_cache.get_query_embeddings(["ja tenho", "nova", "outra"])

    assert pedidos == [["nova", "outra"]], "pediu ao provider algo que ja estava em cache"
    assert saida[0][0] == 1.0, "perdeu a ordem: o item cacheado saiu do lugar"
    assert len(saida) == 3


# ---------------------------------------------------------------------------
# 4. Isolamento por tenant
#
# Nao e regressao: e a propriedade que o case study afirma publicamente e que
# nao pode ser quebrada sem alguem perceber.
# ---------------------------------------------------------------------------

def test_as_duas_pernas_da_busca_filtram_por_user_id():
    sql = _sql_do_hybrid_search()
    assert sql.count("c.user_id = CAST(:user_id AS uuid)") >= 2, (
        "semantica e keyword precisam AMBAS filtrar por dono antes do RRF"
    )


def test_retrieve_documents_exige_user_id():
    """Foi a ausencia deste parametro que deixava o corretive_flow (removido)
    quebrado sem ninguem notar."""
    from app.core.rag.retriever import retrieve_documents

    parametro = inspect.signature(retrieve_documents).parameters["user_id"]
    assert parametro.default is inspect.Parameter.empty, "user_id virou opcional"


# ---------------------------------------------------------------------------
# 5. Custo
# ---------------------------------------------------------------------------

def test_enriquecimento_marca_o_documento_como_cacheavel():
    """Sem cache_control, o documento inteiro (~12,5k tokens) e cobrado como
    input novo em cada chunk: ~US$ 0,69 por PDF de 30 paginas em vez de ~0,10."""
    from app.core.ingestion import chunker

    fonte = inspect.getsource(chunker.enrich_chunks_with_context)
    assert "cache_control" in fonte


def test_primeiro_chunk_roda_sozinho_para_aquecer_o_cache():
    """Disparar todos de uma vez faz todos errarem o cache ao mesmo tempo e
    cada um paga a gravacao — o contrario do que se quer."""
    from app.core.ingestion import chunker

    fonte = inspect.getsource(chunker.enrich_chunks_with_context)
    assert "chunks[0]" in fonte and "ThreadPoolExecutor" in fonte


def test_enriquecimento_preserva_a_ordem_dos_chunks(monkeypatch):
    """add_chunks casa texts[i] com enriched_texts[i]. Paralelizar sem cuidado
    embaralharia o texto de um chunk com o embedding de outro."""
    from app.core.ingestion import chunker

    monkeypatch.setattr(
        chunker,
        "_contexto_de_um_chunk",
        lambda bloco, texto, meta: f"ctx-{meta['chunk_index']}\n\n{texto}",
    )

    entrada = [(f"chunk {i}", {"chunk_index": i, "page": 1}) for i in range(12)]
    saida = chunker.enrich_chunks_with_context(entrada, "documento inteiro", "titulo")

    assert len(saida) == len(entrada)
    for i, (texto, meta) in enumerate(saida):
        assert meta["chunk_index"] == i
        assert texto.endswith(f"chunk {i}"), f"posicao {i} recebeu o texto errado"


def test_rewrite_de_query_so_roda_se_houver_busca_web():
    """Sem TAVILY_API_KEY o rewrite era calculado, exibido e jogado fora: uma
    chamada paga de LLM por pergunta, sem efeito na resposta."""
    fonte = (BACKEND / "app/api/routes/chat.py").read_text()
    assert "if needs_web and settings.tavily_api_key:" in fonte


# ---------------------------------------------------------------------------
# 6. Teto diario global
# ---------------------------------------------------------------------------

def test_chat_consome_teto_antes_de_qualquer_chamada_paga():
    fonte = (BACKEND / "app/api/routes/chat.py").read_text()
    arvore = ast.parse(fonte)
    corpo = next(
        n for n in ast.walk(arvore)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "chat"
    )
    trecho = ast.get_source_segment(fonte, corpo) or ""
    pos_teto = trecho.find('budget.consumir("chat"')
    pos_stream = trecho.find("generate_sse")
    assert pos_teto != -1, "rota de chat sem teto diario"
    assert pos_teto < pos_stream, "teto consumido depois de comecar a responder"


def test_erro_do_provider_nao_vaza_para_o_visitante():
    """Uma mensagem de rate limit da Voyage, com link do dashboard de billing,
    apareceu na tela do visitante no meio do stream."""
    fonte = (BACKEND / "app/api/routes/chat.py").read_text()
    assert '_sse("error", {"message": str(e)})' not in fonte


def test_cadastro_tem_rate_limit():
    """Cadastro aberto e gratuito sem limite e fila infinita de contas."""
    fonte = (BACKEND / "app/api/routes/auth.py").read_text()
    arvore = ast.parse(fonte)
    for nome in ("register", "login"):
        fn = next(
            n for n in ast.walk(arvore)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == nome
        )
        decoradores = [ast.dump(d) for d in fn.decorator_list]
        assert any("limit" in d for d in decoradores), f"/{nome} sem rate limit"


def test_uvicorn_confia_no_proxy_para_enxergar_o_ip_real():
    """Sem isto o slowapi chaveia todo mundo no IP do Traefik: um unico balde
    de 30/minuto para o mundo inteiro."""
    dockerfile = (BACKEND / "Dockerfile").read_text()
    assert "--forwarded-allow-ips" in dockerfile


# ---------------------------------------------------------------------------
# 7. Multimodal: o que e embedado, e o que e so texto de apoio
#
# Antes, imagem virava legenda escrita por um LLM e SO a legenda era indexada:
# tudo que a legenda nao mencionava deixava de existir para a busca. E o
# caminho estava morto duas vezes — pacote `google-generativeai` descontinuado
# pelo Google e id de modelo (`gemini-2.5-flash-preview-04-17`) ja retirado.
# ---------------------------------------------------------------------------

def test_texto_e_imagem_compartilham_o_mesmo_espaco_vetorial():
    """Modelos diferentes para texto e imagem produzem vetores incomparaveis:
    a busca por texto nunca encontraria uma figura."""
    from app.config.settings import Settings

    doc = Settings.model_fields["voyage_doc_model"].default
    query = Settings.model_fields["voyage_query_model"].default
    assert doc == query, "doc e query em modelos distintos = espacos incomparaveis"
    assert "multimodal" in doc, "modelo so-de-texto nao consegue embedar imagem"


def test_gemini_saiu_por_completo():
    """Pacote descontinuado e id de modelo retirado nao voltam pela porta dos
    fundos."""
    linhas = [
        l.strip()
        for l in (BACKEND / "requirements.txt").read_text().splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    assert not any(l.startswith("google-generativeai") for l in linhas)

    from app.config.settings import Settings

    assert "gemini_model" not in Settings.model_fields
    assert "google_api_key" not in Settings.model_fields

    fonte = (BACKEND / "app/core/ingestion/multimodal.py").read_text()
    assert "genai" not in fonte


def test_imagem_entra_no_indice_mesmo_sem_descricao():
    """O vetor vem da imagem. Falha ao descrever custa recall no BM25, nao o
    documento inteiro — antes, sem legenda nao havia documento nenhum."""
    fonte = (BACKEND / "app/api/routes/documents.py").read_text()
    assert '"sequencia": ([descricao, imagem] if descricao else [imagem])' in fonte


def test_embed_images_manda_a_imagem_e_nao_so_a_legenda(monkeypatch):
    from app.services import embedding

    capturado = {}

    class ClienteFalso:
        def multimodal_embed(self, inputs, model, input_type):
            capturado["inputs"] = inputs
            return type("R", (), {"embeddings": [[0.0] * 1024 for _ in inputs]})()

    monkeypatch.setattr(embedding, "_get_client", lambda: ClienteFalso())

    class ImagemFalsa:
        pass

    img = ImagemFalsa()
    embedding.embed_images([img], legendas=["um grafico"])

    sequencia = capturado["inputs"][0]
    assert img in sequencia, "a imagem nao foi enviada ao modelo"
    assert "um grafico" in sequencia, "a legenda deveria acompanhar a imagem"


def test_pagina_escaneada_nao_some_do_indice():
    """pypdf devolve string vazia em pagina que e so imagem. Antes essas
    paginas sumiam sem erro; 'o documento nao diz' respondia algo que estava
    escrito na pagina."""
    from app.core.ingestion.pdf_processor import Pagina, extrair_paginas

    assert callable(extrair_paginas)
    assert Pagina(numero=1, texto="", imagem=object()).escaneada is True
    assert Pagina(numero=1, texto="tem texto").escaneada is False


def test_audio_usa_deepgram_porque_voyage_nao_cobre_audio():
    from app.core.ingestion.multimodal import transcrever_audio
    from app.config.settings import Settings

    assert "deepgram_api_key" in Settings.model_fields
    fonte = inspect.getsource(transcrever_audio)
    assert "api.deepgram.com" in fonte
