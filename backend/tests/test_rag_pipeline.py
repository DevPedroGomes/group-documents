"""Testes que prendem os defeitos encontrados na auditoria de 2026-08-06.

Todos aqui sao regressoes reais que estavam em PRODUCAO, nao casos hipoteticos.
Nenhum toca a rede: o que cada um verifica e a logica que quebrou, e a logica
quebrou em lugares onde uma chamada de API nao teria ajudado a perceber.

O criterio para um teste entrar aqui: se ele tivesse existido, o bug nao teria
chegado ao ar.
"""

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


def test_rrf_saudavel_nao_e_baixa_confianca():
    """O sinal ficava permanentemente ligado, pagando um rewrite por pergunta."""
    docs = [_doc(0.03, "rrf", "a"), _doc(0.02, "rrf", "b")]
    _, baixa_confianca = grade_documents(docs)
    assert baixa_confianca is False


def test_rrf_com_um_trecho_so_e_baixa_confianca():
    _, baixa_confianca = grade_documents([_doc(0.03, "rrf", "a")])
    assert baixa_confianca is True


def test_cohere_com_algum_acima_do_limiar_nao_e_baixa_confianca():
    """O gatilho antigo ("menos da metade passou") disparava aqui, no caso normal:
    um trecho bom e quatro de enchimento."""
    docs = [_doc(0.9, "cohere", "a")] + [_doc(0.1, "cohere", f"x{i}") for i in range(4)]
    mantidos, baixa_confianca = grade_documents(docs)
    assert [d["id"] for d in mantidos] == ["a"]
    assert baixa_confianca is False


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
    mantidos, baixa_confianca = grade_documents(docs)
    assert mantidos, "gerador ficaria sem contexto nenhum"
    assert baixa_confianca is True


def test_lote_vazio_e_baixa_confianca():
    mantidos, baixa_confianca = grade_documents([])
    assert mantidos == []
    assert baixa_confianca is True


# ---------------------------------------------------------------------------
# 2. O texto que chega ao gerador
#
# `hybrid_search` devolvia left(COALESCE(enriched_content, content), 500) e esse
# campo vai direto para o prompt. Chunks tem 500 TOKENS (~2000 chars), entao o
# modelo recebia menos de um terco, cortado no meio de uma frase. Com
# enriquecimento o prefixo gerado pela IA comia ~300 dos 500 caracteres.
# ---------------------------------------------------------------------------

# Coberto com SQL real em tests/test_integracao_busca.py
# (test_snippet_e_o_trecho_cru_inteiro).


# ---------------------------------------------------------------------------
# 3. Uma requisicao de embedding por pergunta, nao uma por variante
#
# O retriever pedia o embedding de cada variante do multi-query numa chamada
# separada: 4 requisicoes por pergunta contra o limite de 3/minuto do plano
# Voyage sem meio de pagamento. Toda pergunta INEDITA falhava; repetida
# funcionava, porque vinha do cache — o que fazia parecer intermitente.
# ---------------------------------------------------------------------------

# Uma chamada de embedding com todas as consultas: prendido pela rota em
# tests/test_chat_pipeline.py (test_seguimento_busca_com_a_pergunta_autocontida).


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

# As duas pernas filtrando por dono: tests/test_integracao_busca.py
# (test_trecho_de_outro_usuario_nunca_volta_em_nenhuma_perna), com SQL real.


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


# O rewrite so roda com baixa confianca e reconsulta o acervo: prendido pela
# rota em tests/test_chat_pipeline.py (secao "CRAG").


# ---------------------------------------------------------------------------
# 6. Teto diario global
# ---------------------------------------------------------------------------

# Teto consumido antes de qualquer chamada paga, e erro do provider que nao vaza
# para a tela: pela rota em tests/test_chat_pipeline.py
# (test_teto_diario_estourado_nao_chama_nada_pago,
# test_erro_do_provider_nao_vaza_para_o_visitante).


@pytest.mark.parametrize("rota,corpo,sem_limite", [
    ("/auth/register", {"email": "a@exemplo.com.br", "password": "curta"}, 400),
    ("/auth/login", {"email": "a@exemplo.com.br", "password": "qualquer-senha-longa"}, 401),
])
def test_cadastro_e_login_tem_rate_limit(limiter_em_memoria, monkeypatch, rota, corpo, sem_limite):
    """Cadastro aberto e gratuito sem limite e fila infinita de contas. Pela
    rota: as cinco primeiras chegam ao handler, a sexta volta 429."""
    from fastapi.testclient import TestClient

    from app.api.routes import auth
    from app.main import create_app
    from tests.motor_falso import MotorFalso

    monkeypatch.setattr(auth, "engine", MotorFalso(escalar=None))
    cliente = TestClient(create_app())

    codigos = [cliente.post(rota, json=corpo).status_code for _ in range(6)]

    assert codigos == [sem_limite] * 5 + [429]


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

def test_texto_e_imagem_compartilham_o_mesmo_espaco_vetorial(monkeypatch):
    """Modelos diferentes para documento, imagem e pergunta produzem vetores
    incomparaveis: a busca por texto nunca encontraria uma figura. Os tres
    caminhos chamam o provider com o MESMO modelo, e ele e multimodal."""
    from PIL import Image

    from app.services import embedding

    modelos = []

    class ClienteFalso:
        def multimodal_embed(self, inputs, model, input_type):
            modelos.append((input_type, model))
            return type("R", (), {"embeddings": [[0.0] * 1024 for _ in inputs]})()

    monkeypatch.setattr(embedding, "_get_client", lambda: ClienteFalso())
    embedding.embed_documents(["trecho"])
    embedding.embed_images([Image.new("RGB", (2, 2))])
    embedding.embed_queries(["pergunta"])

    assert [t for t, _ in modelos] == ["document", "document", "query"]
    assert len({m for _, m in modelos}) == 1, f"modelos distintos: {modelos}"
    assert "multimodal" in modelos[0][1], "modelo so-de-texto nao consegue embedar imagem"


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
    documento inteiro — antes, sem legenda nao havia documento nenhum.

    `process_ingestion` mora em `app/jobs/ingestao.py` desde a Fase 2 Task 5;
    o teste segue o codigo."""
    fonte = (BACKEND / "app/jobs/ingestao.py").read_text()
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


# ---------------------------------------------------------------------------
# 8. Traducao de bloco de imagem entre os dois formatos de provider
#
# Os call sites escrevem no formato Anthropic. Producao roda no OpenRouter, que
# fala OpenAI-compat, onde imagem tem OUTRA forma. Sem traduzir, o bloco era
# descartado em silencio: o modelo recebia so o texto "descreva esta imagem",
# sem imagem, e devolvia a descricao de uma imagem inventada — plausivel,
# errada, e indo direto para o indice.
# ---------------------------------------------------------------------------

def test_bloco_de_imagem_e_traduzido_para_o_formato_openai():
    from app.core.llm_client import _to_openai_messages

    mensagens = [{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/png", "data": "QUJD"}},
            {"type": "text", "text": "descreva"},
        ],
    }]
    saida = _to_openai_messages(mensagens, None)
    blocos = saida[0]["content"]

    assert blocos[0]["type"] == "image_url", "bloco de imagem seguiria e seria descartado"
    assert blocos[0]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert blocos[1] == {"type": "text", "text": "descreva"}


def test_traducao_nao_mexe_em_conteudo_de_texto_simples():
    from app.core.llm_client import _to_openai_messages

    mensagens = [{"role": "user", "content": "texto puro"}]
    assert _to_openai_messages(mensagens, None) == mensagens


def test_bloco_com_cache_control_sobrevive_a_traducao():
    """O marcador de cache do enriquecimento nao pode ser perdido no caminho."""
    from app.core.llm_client import _to_openai_messages

    mensagens = [{
        "role": "user",
        "content": [{"type": "text", "text": "doc", "cache_control": {"type": "ephemeral"}}],
    }]
    bloco = _to_openai_messages(mensagens, None)[0]["content"][0]
    assert bloco.get("cache_control") == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# A busca por palavra-chave e o chunk que nao cabia
# ---------------------------------------------------------------------------


# A config textual do trigger contra a da consulta, e o piso medido do
# conjunto-ouro, rodam com SQL real em tests/test_integracao_busca.py.


def test_sentenca_sem_pontuacao_nao_vira_chunk_ilimitado():
    """Uma pagina de tabela ou de contrato em caixa alta e UMA sentenca.

    O laco so fecha um chunk quando ja ha algo acumulado; com a lista vazia a
    condicao e falsa e a sentenca entrava inteira, de qualquer tamanho. O chunk
    estourava o limite do embedding, e o trecho que chegava ao gerador era a
    pagina toda.
    """
    from app.core.ingestion.chunker import _token_count, chunk_text

    teto = 500
    sem_pontuacao = "PALAVRA " * 4000
    chunks = chunk_text(sem_pontuacao, max_tokens=teto)

    assert len(chunks) > 1, "a sentenca gigante continuou saindo em um chunk so"
    maior = max(_token_count(c) for c in chunks)
    assert maior <= teto, f"chunk com {maior} tokens acima do teto de {teto}"


def test_texto_normal_continua_saindo_inteiro():
    """O teto nao pode picotar quem ja cabia."""
    from app.core.ingestion.chunker import chunk_text

    texto = "Primeira frase. Segunda frase aqui. Terceira e ultima."
    assert chunk_text(texto, max_tokens=500) == [texto]


# ---------------------------------------------------------------------------
# A data do documento atravessa o pipeline
#
# `hybrid_search` devolve `document_date`; o recorte no tempo e o aviso de
# divergencia dependem dela chegar ate a citacao, passando pelo retriever e
# pelo reranker.
# ---------------------------------------------------------------------------

def _trechos_com_data(n: int) -> list[dict]:
    return [
        {"id": f"c{i}", "document_id": f"d{i}", "document_title": f"t{i}", "page": 1,
         "snippet": f"trecho {i}", "document_date": f"2025-0{i + 1}-01",
         "relevance_score": 0.03 - i * 0.001, "score_scale": "rrf"}
        for i in range(n)
    ]


def test_document_date_sobrevive_ao_retriever_sem_reranker(monkeypatch):
    from app.config.settings import get_settings
    from app.core.rag import retriever

    monkeypatch.setattr(get_settings(), "cohere_api_key", None)
    monkeypatch.setattr(retriever, "generate_multi_queries", lambda q, h=None: [q])
    monkeypatch.setattr(retriever, "get_query_embeddings", lambda qs: [[0.0] * 1024 for _ in qs])
    monkeypatch.setattr(retriever, "hybrid_search", lambda **kw: _trechos_com_data(3))

    saida = retriever.retrieve_documents("pergunta", user_id="u", top_k=2).documents

    assert [t["document_date"] for t in saida] == ["2025-01-01", "2025-02-01"]


@pytest.mark.parametrize("limite,esperado", [(15, 100), (60, 120), (450, 900), (600, 1000)])
def test_ef_search_e_o_dobro_do_limit_entre_piso_e_teto(limite, esperado):
    """O teto e o maximo que o pgvector aceita; passar dele e erro na consulta."""
    from app.services.vector_store import _ef_search

    assert _ef_search(limite) == esperado


def test_cache_de_embedding_separa_modelo_e_lado(monkeypatch):
    """Trocar o modelo nao pode servir vetor do modelo antigo (outro espaco
    vetorial), nem o vetor de documento pode responder por uma query."""
    from app.config.settings import get_settings
    from app.services import embedding_cache

    class RedisFalso(dict):
        def setex(self, chave, _ttl, valor):
            self[chave] = valor

    redis = RedisFalso()
    monkeypatch.setattr(embedding_cache, "_get_redis", lambda: redis)
    settings = get_settings()
    monkeypatch.setattr(settings, "voyage_doc_model", "modelo-a")

    embedding_cache.cache_embedding("Frete gratis?", [1.0, 2.0])

    assert embedding_cache.get_cached_embedding("frete gratis?") == [1.0, 2.0]
    assert embedding_cache.get_cached_embedding("frete gratis?", input_type="document") is None
    monkeypatch.setattr(settings, "voyage_doc_model", "modelo-b")
    assert embedding_cache.get_cached_embedding("frete gratis?") is None


# ---------------------------------------------------------------------------
# Reranker na API v2 da Cohere
# ---------------------------------------------------------------------------

def _cohere_falso(monkeypatch, *, resultados=None, erro=None):
    """Troca o modulo `cohere` por um que so tem `ClientV2`, cujo `rerank`
    confere os argumentos contra a assinatura do `V2Client.rerank` INSTALADO:
    argumento que a v2 nao aceita (como o `return_documents` da v1) quebra aqui.
    Modulo falso, e nao setattr no real, porque o import preguicoso do SDK
    depende de `_lzma`, que nem todo Python local tem."""
    import inspect
    import sys
    from types import SimpleNamespace

    from cohere.v2.client import V2Client

    from app.core.rag import reranker

    assinatura = inspect.signature(V2Client.rerank)
    chamadas: list[dict] = []
    criados: list[str] = []

    class ClienteV2Falso:
        def __init__(self, api_key=None):
            criados.append(api_key)

        def rerank(self, **kwargs):
            assinatura.bind(self, **kwargs)
            chamadas.append(kwargs)
            if erro:
                raise erro
            return SimpleNamespace(results=[
                SimpleNamespace(index=i, relevance_score=s) for i, s in resultados
            ])

    monkeypatch.setitem(sys.modules, "cohere", SimpleNamespace(ClientV2=ClienteV2Falso))
    monkeypatch.setattr(reranker, "_client", None)
    return chamadas, criados


def _com_cohere(monkeypatch, chave="chave-cohere"):
    from app.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "cohere_api_key", chave)
    monkeypatch.setattr(settings, "enable_reranking", True)


def test_rerank_pela_v2_reordena_e_preserva_a_data_do_documento(monkeypatch):
    from app.core.rag import retriever

    _com_cohere(monkeypatch)
    chamadas, criados = _cohere_falso(monkeypatch, resultados=[(2, 0.91), (0, 0.42)])
    monkeypatch.setattr(retriever, "generate_multi_queries", lambda q, h=None: [q])
    monkeypatch.setattr(retriever, "get_query_embeddings", lambda qs: [[0.0] * 1024 for _ in qs])
    monkeypatch.setattr(retriever, "hybrid_search", lambda **kw: _trechos_com_data(3))

    saida = retriever.retrieve_documents("pergunta", user_id="u", top_k=2).documents

    assert criados == ["chave-cohere"]
    assert chamadas[0]["documents"] == ["trecho 0", "trecho 1", "trecho 2"]
    assert chamadas[0]["top_n"] == 2
    assert [(t["id"], t["relevance_score"], t["score_scale"]) for t in saida] == [
        ("c2", 0.91, "cohere"), ("c0", 0.42, "cohere"),
    ]
    assert [t["document_date"] for t in saida] == ["2025-03-01", "2025-01-01"]


def test_rerank_sem_chave_fica_na_ordem_do_rrf_sem_criar_cliente(monkeypatch):
    from app.core.rag.reranker import rerank_documents

    _com_cohere(monkeypatch, chave=None)
    chamadas, criados = _cohere_falso(monkeypatch, resultados=[])

    saida = rerank_documents("pergunta", _trechos_com_data(3), top_n=2)

    assert [t["id"] for t in saida] == ["c0", "c1"]
    assert saida[0]["score_scale"] == "rrf"
    assert criados == [] and chamadas == []


def test_rerank_que_falha_cai_na_ordem_do_rrf(monkeypatch):
    from app.core.rag.reranker import rerank_documents

    _com_cohere(monkeypatch)
    _cohere_falso(monkeypatch, erro=RuntimeError("503 da Cohere"))

    saida = rerank_documents("pergunta", _trechos_com_data(3), top_n=2)

    assert [(t["id"], t["score_scale"]) for t in saida] == [("c0", "rrf"), ("c1", "rrf")]


def test_limpeza_da_pergunta_tira_funcionais_sem_acento_e_informais():
    """As listas de stopwords do Postgres so tem a forma acentuada ("não",
    "até") e nao tem "pra"/"então"; com OR, cada uma viraria termo da busca."""
    from app.services.vector_store import limpar_consulta_textual

    saida = limpar_consulta_textual("Ate quando NAO posso devolver pra voce? Entao, tá, é isso")
    assert saida.split() == ["quando", "posso", "devolver", "?", ",", ",", "isso"]
    assert limpar_consulta_textual("frete gratis acima de 150") == "frete gratis acima de 150"


# ---------------------------------------------------------------------------
# Condensacao da pergunta de seguimento
# ---------------------------------------------------------------------------

def _multi_query_falso(monkeypatch, resposta):
    from app.config.settings import get_settings
    from app.core.rag import retriever

    prompts: list[str] = []

    def falso(**kw):
        prompts.append(kw["messages"][-1]["content"])
        return resposta

    monkeypatch.setattr(get_settings(), "multi_query_count", 2)
    monkeypatch.setattr(retriever, "chat_complete", falso)
    return retriever, prompts


def test_condensacao_so_ve_as_6_ultimas_mensagens_e_corta_as_longas(monkeypatch):
    retriever, prompts = _multi_query_falso(monkeypatch, "autocontida\nv1\nv2")
    historico = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"mensagem-{i} " + "x" * 2000}
                 for i in range(10)]

    consultas = retriever.generate_multi_queries("e o outro?", historico)

    assert consultas == ["autocontida", "v1", "v2", "e o outro?"]
    assert "mensagem-3 " not in prompts[0]
    assert all(f"mensagem-{i} " in prompts[0] for i in range(4, 10))
    assert "x" * 700 not in prompts[0], "mensagem longa entrou inteira no prompt"


def test_condensacao_limpa_rotulo_numeracao_e_repeticao(monkeypatch):
    retriever, _ = _multi_query_falso(
        monkeypatch,
        "Standalone question: \"qual o prazo em 2025?\"\n1. prazo 2025\n- QUAL O PRAZO EM 2025?\n3.5% de multa\n",
    )

    consultas = retriever.generate_multi_queries("e em 2025?", [{"role": "user", "content": "qual o prazo?"}])

    assert consultas == ["qual o prazo em 2025?", "prazo 2025", "3.5% de multa", "e em 2025?"]


@pytest.mark.parametrize("resposta", [
    "Here is the standalone question:\nqual o prazo em 2025?\nprazo 2025",
    "Rewritten question: qual o prazo em 2025?\nprazo 2025",
    "**Standalone question:** qual o prazo em 2025?\nprazo 2025",
    "**Standalone question:**\nqual o prazo em 2025?\nprazo 2025",
    "### Standalone question\nqual o prazo em 2025?\n- prazo 2025",
    "Sure! Here are the queries:\n\n1. qual o prazo em 2025?\n2. prazo 2025",
    "Pergunta autocontida: `qual o prazo em 2025?`\nConsulta 1: prazo 2025",
    "\u201cqual o prazo em 2025?\u201d\nQuery 2: prazo 2025",
])
def test_preambulo_e_rotulo_do_modelo_nao_viram_consulta(monkeypatch, resposta):
    """Com historico a primeira linha manda na busca inteira (embedding,
    palavra-chave, rerank, reescrita): preambulo ali estragava tudo."""
    retriever, _ = _multi_query_falso(monkeypatch, resposta)

    consultas = retriever.generate_multi_queries("e em 2025?", [{"role": "user", "content": "qual o prazo?"}])

    assert consultas == ["qual o prazo em 2025?", "prazo 2025", "e em 2025?"]


@pytest.mark.parametrize("resposta", ["Here are the queries:", "**Standalone question:**\n\n", "### Query"])
def test_resposta_so_com_preambulo_busca_com_a_pergunta_original(monkeypatch, resposta):
    retriever, _ = _multi_query_falso(monkeypatch, resposta)

    assert retriever.generate_multi_queries("e em 2025?", [{"role": "user", "content": "oi"}]) == ["e em 2025?"]


def test_original_nao_se_repete_quando_ja_era_autocontida(monkeypatch):
    retriever, _ = _multi_query_falso(monkeypatch, "Qual o prazo em 2025?\nprazo 2025")

    consultas = retriever.generate_multi_queries("qual o prazo em 2025?", [{"role": "user", "content": "oi"}])

    assert consultas == ["Qual o prazo em 2025?", "prazo 2025"]


def test_condensacao_vazia_usa_a_pergunta_original(monkeypatch):
    retriever, _ = _multi_query_falso(monkeypatch, "  \n\n")

    assert retriever.generate_multi_queries("e em 2025?", [{"role": "user", "content": "oi"}]) == ["e em 2025?"]


def test_sem_historico_a_pergunta_original_abre_a_lista(monkeypatch):
    retriever, prompts = _multi_query_falso(monkeypatch, "v1\nv2\nv3")

    assert retriever.generate_multi_queries("qual o prazo?") == ["qual o prazo?", "v1", "v2"]
    assert "standalone" not in prompts[0]


# ---------------------------------------------------------------------------
# Passo corretivo: reconsulta do acervo com a pergunta reescrita
# ---------------------------------------------------------------------------

def _reconsulta_falsa(monkeypatch, novos):
    from app.core.rag import retriever

    buscas: list[str] = []
    embeds: list[list[str]] = []

    def busca(**kw):
        buscas.append(kw["query_text"])
        return [dict(t) for t in novos]

    monkeypatch.setattr(retriever, "hybrid_search", busca)
    monkeypatch.setattr(retriever, "get_query_embeddings",
                        lambda qs: embeds.append(list(qs)) or [[0.0] * 4 for _ in qs])
    return retriever, buscas, embeds


def _t(ident, score, escala="rrf"):
    return {"id": ident, "document_id": f"d-{ident}", "document_title": ident, "page": 1,
            "snippet": f"texto {ident}", "document_date": "2025-01-01",
            "relevance_score": score, "score_scale": escala}


def test_reconsulta_sem_reranker_faz_uma_busca_e_funde_pelo_maior_score(monkeypatch):
    _com_cohere(monkeypatch, chave=None)
    retriever, buscas, embeds = _reconsulta_falsa(monkeypatch, [_t("b", 0.03), _t("c", 0.015)])

    saida = retriever.reconsultar(
        "pergunta reescrita", "pergunta", [_t("a", 0.02), _t("b", 0.01)], user_id="u", top_k=5,
    )

    assert buscas == ["pergunta reescrita"], "reconsulta virou multi-query"
    assert embeds == [["pergunta reescrita"]]
    assert [(t["id"], t["relevance_score"]) for t in saida] == [("b", 0.03), ("a", 0.02), ("c", 0.015)]


def test_reconsulta_com_reranker_reordena_a_uniao_contra_a_pergunta(monkeypatch):
    _com_cohere(monkeypatch)
    chamadas, _ = _cohere_falso(monkeypatch, resultados=[(0, 0.93), (2, 0.4)])
    retriever, _, _ = _reconsulta_falsa(monkeypatch, [_t("b", 0.03), _t("c", 0.015)])

    saida = retriever.reconsultar(
        "pergunta reescrita", "pergunta autocontida", [_t("a", 0.2, "cohere")], user_id="u", top_k=2,
    )

    (chamada,) = chamadas
    assert chamada["query"] == "pergunta autocontida", "o score deixaria de medir a pergunta da pessoa"
    assert sorted(chamada["documents"]) == ["texto a", "texto b", "texto c"]
    assert {t["score_scale"] for t in saida} == {"cohere"}
    assert [t["relevance_score"] for t in saida] == [0.93, 0.4]


def test_reconsulta_com_rerank_quebrado_nao_mistura_escalas(monkeypatch):
    _com_cohere(monkeypatch)
    _cohere_falso(monkeypatch, erro=RuntimeError("503 da Cohere"))
    retriever, _, _ = _reconsulta_falsa(monkeypatch, [_t("b", 0.03)])
    anteriores = [_t("a", 0.2, "cohere")]

    saida = retriever.reconsultar("reescrita", "pergunta", anteriores, user_id="u", top_k=5)

    assert saida == anteriores


def test_reconsulta_sem_resultado_novo_mantem_o_lote(monkeypatch):
    _com_cohere(monkeypatch, chave=None)
    retriever, _, _ = _reconsulta_falsa(monkeypatch, [])

    assert retriever.reconsultar("reescrita", "pergunta", [_t("a", 0.02)], user_id="u")[0]["id"] == "a"
