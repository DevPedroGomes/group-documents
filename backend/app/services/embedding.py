"""Embeddings locais: texto e imagem no mesmo espaco vetorial, sem provider.

`jinaai/jina-clip-v1` via FastEmbed/ONNX. Duas torres do MESMO modelo — e por
isso que texto e imagem saem comparaveis, que e o que permite uma pergunta
escrita recuperar uma figura.

Por que saiu da Voyage: numa unica auditoria, tres provedores quebraram este
app de tres formas diferentes (rate limit, modelo aposentado, endpoint
retirado). Dessas dependencias, embedding era a UNICA com lock-in real —
trocar de modelo obriga a re-embedar o corpus. As outras se trocam numa tarde.

Medido nesta VPS (2 vCPU): consulta 34 ms, chunk 413 ms, imagem 385 ms. A
consulta ficou mais rapida que a chamada de rede que substituiu. A ingestao
ficou mais lenta, mas ja era dominada pelas chamadas de LLM do enriquecimento.

Duas salvaguardas, ambas por causa dos 2 nucleos:

- `_VAGAS`: no maximo N embeddings simultaneos. Sem isso a ingestao de um PDF
  compete com aurora, scraper e searcher pela CPU da mesma maquina.
- A torre de IMAGEM carrega sob demanda. A maioria dos documentos e texto puro,
  e manter os dois modelos residentes custa ~1,7 GB de RSS por um caminho que
  roda raramente.
"""

from __future__ import annotations

import logging
import os
import threading

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

MODELO = "jinaai/jina-clip-v1"

# Teto de embeddings concorrentes. 2 nucleos, e as outras demos do portfolio
# rodam na mesma maquina.
_VAGAS = threading.Semaphore(
    max(1, int(os.getenv("EMBEDDING_MAX_CONCURRENCY", "2")))
)

_torre_texto = None
_torre_imagem = None
_lock_carga = threading.Lock()


def _cache_dir() -> str | None:
    return os.getenv("FASTEMBED_CACHE_PATH") or None


def _texto():
    """Torre de texto. Carrega uma vez, na primeira chamada."""
    global _torre_texto
    if _torre_texto is None:
        with _lock_carga:
            if _torre_texto is None:
                from fastembed import TextEmbedding

                logger.info("Carregando torre de TEXTO (%s)...", MODELO)
                _torre_texto = TextEmbedding(
                    model_name=MODELO,
                    cache_dir=_cache_dir(),
                    # 1 thread por sessao ONNX: o paralelismo util aqui vem do
                    # semaforo, nao de cada modelo brigando pelos 2 nucleos.
                    threads=1,
                )
    return _torre_texto


def _imagem():
    """Torre de imagem. So carrega quando chega a primeira imagem."""
    global _torre_imagem
    if _torre_imagem is None:
        with _lock_carga:
            if _torre_imagem is None:
                from fastembed import ImageEmbedding

                logger.info("Carregando torre de IMAGEM (%s)...", MODELO)
                _torre_imagem = ImageEmbedding(
                    model_name=MODELO,
                    cache_dir=_cache_dir(),
                    threads=1,
                )
    return _torre_imagem


def _assert_dim(vector: list[float]) -> None:
    """Falha cedo e com mensagem clara se a dimensao mudar.

    Sem isto o erro so aparece no INSERT, como
    `expected 768 dimensions, not 512` — que aponta para o banco e nao para o
    modelo, que e a causa real. Foi assim que a ingestao ficou 100% quebrada
    por meses sem ninguem notar (ver migrations/002).
    """
    esperado = get_settings().embedding_dimensions
    if len(vector) != esperado:
        raise RuntimeError(
            f"Embedding dim mismatch: '{MODELO}' devolveu {len(vector)} dims, "
            f"mas o schema espera {esperado}. Alinhe EMBEDDING_DIMENSIONS, o "
            f"vector(N) das migrations e o modelo — os tres tem que bater."
        )


def _embed_textos(textos: list[str]) -> list[list[float]]:
    if not textos:
        return []
    with _VAGAS:
        vetores = [list(map(float, v)) for v in _texto().embed(textos)]
    if len(vetores) != len(textos):
        raise RuntimeError(
            f"Embedding count mismatch: esperava {len(textos)}, veio {len(vetores)}"
        )
    _assert_dim(vetores[0])
    return vetores


def _embed_imagens(imagens: list) -> list[list[float]]:
    """`imagens` sao PIL.Image (o FastEmbed tambem aceita caminho de arquivo)."""
    if not imagens:
        return []
    with _VAGAS:
        vetores = [list(map(float, v)) for v in _imagem().embed(imagens)]
    if len(vetores) != len(imagens):
        raise RuntimeError(
            f"Embedding count mismatch: esperava {len(imagens)}, veio {len(vetores)}"
        )
    _assert_dim(vetores[0])
    return vetores


def _e_imagem(x) -> bool:
    """PIL.Image sem importar Pillow so para checar tipo."""
    return hasattr(x, "size") and hasattr(x, "mode") and not isinstance(x, str)


def embed_sequences(inputs: list[list], input_type: str = "document") -> list[list[float]]:
    """Embeda uma lista de SEQUENCIAS, preservando a ordem de entrada.

    Cada sequencia descreve UM item e pode ser:
      ["texto"]                -> torre de texto
      ["descricao", <imagem>]  -> torre de IMAGEM; a descricao segue existindo
                                  no banco para o BM25, mas nao entra no vetor
      [<imagem>]               -> torre de imagem

    Esta assinatura e a mesma de quando os embeddings vinham da Voyage: o
    retriever, o grader e a ingestao nao sabem que o provider mudou. Foi a
    costura que tornou a troca barata.

    `input_type` existia para a API da Voyage, que prefixava prompts diferentes
    para documento e consulta. O jina-clip-v1 nao faz essa distincao; o
    parametro fica por compatibilidade de chamada.
    """
    if not inputs:
        return []

    # Separa por modalidade preservando a posicao, para devolver na ordem.
    idx_texto, textos = [], []
    idx_imagem, imagens = [], []

    for i, seq in enumerate(inputs):
        imagem = next((x for x in seq if _e_imagem(x)), None)
        if imagem is not None:
            idx_imagem.append(i)
            imagens.append(imagem)
        else:
            partes = [x for x in seq if isinstance(x, str) and x.strip()]
            idx_texto.append(i)
            textos.append(" ".join(partes) if partes else " ")

    saida: list[list[float] | None] = [None] * len(inputs)
    for i, v in zip(idx_texto, _embed_textos(textos)):
        saida[i] = v
    for i, v in zip(idx_imagem, _embed_imagens(imagens)):
        saida[i] = v

    faltando = [i for i, v in enumerate(saida) if v is None]
    if faltando:
        raise RuntimeError(f"Sequencias sem embedding nas posicoes {faltando}")
    return saida  # type: ignore[return-value]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embeda chunks de texto."""
    return _embed_textos(texts)


def embed_images(images: list, legendas: list[str] | None = None) -> list[list[float]]:
    """Embeda imagens.

    `legendas` e aceito por compatibilidade com a chamada antiga, mas NAO entra
    no vetor: as duas torres do jina-clip-v1 embedam uma modalidade cada, sem
    fundir texto e imagem num vetor so como a API da Voyage fazia. A descricao
    continua sendo gravada em `content` e continua servindo ao BM25 — o que
    muda e que ela nao dilui mais o vetor da imagem.
    """
    if legendas and len(legendas) != len(images):
        raise ValueError("legendas e images precisam ter o mesmo tamanho")
    return _embed_imagens(images)


def embed_queries(texts: list[str]) -> list[list[float]]:
    """Embeda varias consultas de uma vez.

    Com provider de rede isto existia para nao estourar o limite de
    requisicoes. Local nao ha limite nenhum, mas o lote continua valendo: uma
    passada do ONNX sobre N textos e mais barata que N passadas.
    """
    return _embed_textos(texts)


def embed_query(text: str) -> list[float]:
    """Atalho de uma consulta so."""
    return _embed_textos([text])[0]


def aquecer() -> None:
    """Carrega a torre de texto no boot.

    Sem isto o PRIMEIRO visitante paga os ~15 s de carga do modelo dentro da
    propria pergunta. A torre de imagem continua sob demanda de proposito.
    """
    try:
        _embed_textos(["aquecimento"])
        logger.info("Torre de texto pronta.")
    except Exception as exc:
        logger.error("Falha ao aquecer o modelo de embedding: %s", exc)
