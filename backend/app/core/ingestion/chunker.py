"""
Semantic chunking with contextual enrichment.

Chunking: tiktoken-based recursive splitting with overlap.
Enrichment: Claude Haiku generates context per chunk (Anthropic's contextual retrieval technique).
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import tiktoken

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_encoder = tiktoken.encoding_for_model("gpt-4o")


def _token_count(text: str) -> int:
    return len(_encoder.encode(text))


def _agrupar(partes: list[str], separador: str, max_tokens: int) -> list[str]:
    """Junta partes consecutivas enquanto couberem no teto."""
    saida: list[str] = []
    atual: list[str] = []
    for parte in partes:
        if atual and _token_count(separador.join(atual + [parte])) > max_tokens:
            saida.append(separador.join(atual))
            atual = [parte]
        else:
            atual.append(parte)
    if atual:
        saida.append(separador.join(atual))
    return saida


def _quebrar_em_sentencas(text: str, max_tokens: int) -> list[str]:
    """Divide em sentencas e impoe um TETO por sentenca.

    O laco de `chunk_text` so fecha um chunk quando `current_chunk` ja tem algo:
    com ele vazio a condicao e falsa e a sentenca entra inteira, qualquer que
    seja o tamanho. Uma pagina sem pontuacao — tabela, balanco, contrato em
    caixa alta — e UMA sentenca, e virava um chunk de tamanho ilimitado:
    estoura o limite do embedding, e o trecho que chega ao gerador vira a
    pagina toda.

    Aqui a sentenca grande demais e partida por LINHA, que e onde uma tabela
    se divide, e so a linha que sozinha estoura o teto e partida por palavras.
    Os pedacos mantem a quebra de linha, entao cada valor segue na linha do
    rotulo dele.
    """
    sentencas = re.split(r"(?<=[.!?])\s+", text)
    saida: list[str] = []

    for sent in sentencas:
        if _token_count(sent) <= max_tokens:
            saida.append(sent)
            continue

        linhas: list[str] = []
        for linha in sent.split("\n"):
            if _token_count(linha) <= max_tokens:
                linhas.append(linha)
            else:
                linhas.extend(_agrupar(linha.split(), " ", max_tokens))
        saida.extend(_agrupar(linhas, "\n", max_tokens))

    return saida


def chunk_text(text: str, max_tokens: int = 500, overlap: int = 100) -> list[str]:
    """
    Split text into chunks of max_tokens with overlap.
    Uses sentence boundaries for semantic coherence.
    """
    sentences = _quebrar_em_sentencas(text, max_tokens)
    chunks = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = _token_count(sent)

        if current_tokens + sent_tokens > max_tokens and current_chunk:
            chunks.append(" ".join(current_chunk))

            # Keep overlap: walk backwards to find sentences that fit in overlap
            overlap_chunk: list[str] = []
            overlap_tokens = 0
            for s in reversed(current_chunk):
                s_tokens = _token_count(s)
                if overlap_tokens + s_tokens > overlap:
                    break
                overlap_chunk.insert(0, s)
                overlap_tokens += s_tokens

            current_chunk = overlap_chunk + [sent]
            current_tokens = overlap_tokens + sent_tokens
        else:
            current_chunk.append(sent)
            current_tokens += sent_tokens

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def chunk_document_pages(
    pages: list[str],
) -> list[tuple[str, dict]]:
    """
    Chunk a document's pages into (text, metadata) tuples.
    Metadata includes page number and chunk index.
    """
    settings = get_settings()
    all_chunks = []
    global_idx = 0

    for page_num, page_text in enumerate(pages, start=1):
        if not page_text.strip():
            continue

        page_chunks = chunk_text(
            page_text,
            max_tokens=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )

        for chunk in page_chunks:
            metadata = {
                "page": page_num,
                "chunk_index": global_idx,
            }
            all_chunks.append((chunk, metadata))
            global_idx += 1

    return all_chunks


# Quanto do documento vai como contexto em cada chamada: ~12,5k tokens.
_JANELA = 50_000
# Palavras do comeco do chunk usadas para acha-lo no texto do documento.
_PALAVRAS_PARA_ACHAR = 12

_INSTRUCAO_CONTEXTO = (
    "Give a short succinct context (2-3 sentences) to situate this chunk "
    "within the overall document. Answer only with the context, no preamble."
)

# Quantos chunks enriquecer em paralelo DEPOIS que o cache esta quente. Modesto
# de proposito: a VPS tem 2 vCPU e estas chamadas ja sao I/O, nao CPU.
_PARALELISMO = 4


def _contexto_de_um_chunk(doc_block: dict, chunk_text_str: str, meta: dict) -> str:
    """Gera o contexto de um chunk. Devolve o chunk cru se a chamada falhar."""
    from app.core.llm_client import chat_complete

    settings = get_settings()
    try:
        contexto = chat_complete(
            model=settings.fast_model,
            max_tokens=150,
            messages=[
                {
                    "role": "user",
                    # Dois blocos, nao uma string: o primeiro carrega o
                    # documento e o marcador de cache, o segundo carrega a parte
                    # que muda a cada chunk. So o que vem DEPOIS do marcador e
                    # cobrado como input novo.
                    "content": [
                        doc_block,
                        {
                            "type": "text",
                            "text": (
                                f"Here is a chunk from this document:\n"
                                f"<chunk>\n{chunk_text_str}\n</chunk>\n\n"
                                f"{_INSTRUCAO_CONTEXTO}"
                            ),
                        },
                    ],
                }
            ],
        ).strip()
        return f"{contexto}\n\n{chunk_text_str}"
    except Exception as e:
        logger.warning(f"Contextual enrichment failed for chunk {meta['chunk_index']}: {e}")
        return chunk_text_str


def _centros(chunks: list[tuple[str, dict]], texto: str) -> list[float]:
    """Onde cada chunk esta no texto do documento, pelo meio dele.

    O chunk nao e substring exata do texto (o chunker junta sentencas com um
    espaco), entao a busca casa as primeiras palavras dele com qualquer
    whitespace entre elas, a partir de onde o chunk anterior comecou. Chunk
    nao achado fica na posicao proporcional a ordem dele.
    """
    centros: list[float] = []
    cursor = 0
    for i, (trecho, _meta) in enumerate(chunks):
        palavras = trecho.split()[:_PALAVRAS_PARA_ACHAR]
        achado = None
        if palavras:
            padrao = re.compile(r"\s+".join(map(re.escape, palavras)))
            achado = padrao.search(texto, cursor) or padrao.search(texto)
        if achado:
            cursor = achado.start()
            centros.append(achado.start() + len(trecho) / 2)
        else:
            centros.append((i + 0.5) / len(chunks) * len(texto))
    return centros


def _janelas(chunks: list[tuple[str, dict]], texto: str) -> list[tuple[str, list[int]]]:
    """O trecho do documento que vai como contexto, e os chunks que o usam.

    Documento que cabe em `_JANELA` vai inteiro, numa janela so. Acima disso o
    corte fixo nos primeiros 50k dava ao chunk do fim um contexto que nao o
    continha. Aqui o documento vira janelas de `_JANELA` caracteres que andam
    de meia em meia janela, e cada chunk usa aquela em cujo meio ele cai: o
    chunk fica dentro dela, com contexto dos dois lados.
    """
    if len(texto) <= _JANELA:
        return [(texto, list(range(len(chunks))))]

    passo = _JANELA // 2
    ultima = -(-(len(texto) - _JANELA) // passo)  # teto: a ultima janela alcanca o fim
    grupos: dict[int, list[int]] = {}
    for i, centro in enumerate(_centros(chunks, texto)):
        k = min(max(round((centro - _JANELA / 2) / passo), 0), ultima)
        grupos.setdefault(k, []).append(i)
    return [(texto[k * passo : k * passo + _JANELA], indices) for k, indices in sorted(grupos.items())]


def enrich_chunks_with_context(
    chunks: list[tuple[str, dict]],
    full_document_text: str,
    document_title: str,
) -> list[tuple[str, dict]]:
    """
    Contextual Retrieval da Anthropic: um modelo rapido (classe Haiku) escreve
    50-100 tokens situando cada chunk no documento, prefixados antes do embedding.
    Reduz falha de recuperacao em ~49%, e ~67% junto com reranking. Vale para
    todo fluxo de texto: PDF, pagina da web e transcricao de audio.

    O detalhe que decide o custo e o prompt caching. O documento (ate 50k
    caracteres, ~12,5k tokens) vai como input de TODA chamada — uma por chunk.
    Sem cache, um PDF de 30 paginas (~50 chunks) manda ~650k tokens de input e
    custa por volta de US$ 0,69 com Haiku 4.5. Marcando o bloco do documento
    como cacheavel, a primeira chamada grava o cache e as seguintes leem a 10%
    do preco: ~US$ 0,10 pelo mesmo documento, ~85% mais barato.

    Por isso o primeiro chunk de cada janela roda SOZINHO: ele e quem grava o
    cache. Disparar todos de uma vez faria todos errarem o cache ao mesmo tempo
    e cada um pagaria a gravacao. Com o cache quente, o resto vai em paralelo.

    Documento maior que a janela: cada chunk recebe o trecho ao redor dele (ver
    `_janelas`). Troca: uma gravacao de cache por janela em vez de uma por
    documento, e contexto local em vez do comeco do documento; os chunks da
    mesma janela continuam dividindo o cache.
    """
    if not chunks:
        return []

    resultados: list[Optional[str]] = [None] * len(chunks)

    for trecho, indices in _janelas(chunks, full_document_text):
        doc_block = {
            "type": "text",
            "text": f'<document title="{document_title}">\n{trecho}\n</document>',
            # Funciona nos dois caminhos do llm_client: nativo Anthropic e
            # OpenRouter (que repassa cache_control por bloco de conteudo).
            # Prompts curtos ficam abaixo do minimo cacheavel e simplesmente
            # nao sao cacheados — sem erro, e sem custo relevante.
            "cache_control": {"type": "ephemeral"},
        }

        # 1) O primeiro chunk da janela sozinho: grava o cache.
        primeiro = indices[0]
        resultados[primeiro] = _contexto_de_um_chunk(doc_block, *chunks[primeiro])

        # 2) O resto da janela em paralelo, ja lendo do cache.
        if len(indices) > 1:
            with ThreadPoolExecutor(max_workers=_PARALELISMO) as pool:
                futuros = {
                    pool.submit(_contexto_de_um_chunk, doc_block, *chunks[i]): i
                    for i in indices[1:]
                }
                for futuro in as_completed(futuros):
                    i = futuros[futuro]
                    try:
                        resultados[i] = futuro.result()
                    except Exception as e:
                        # `_contexto_de_um_chunk` ja trata as suas falhas; isto
                        # e a rede de baixo, para nao perder o chunk se o pool
                        # quebrar por outro motivo.
                        logger.warning(f"Enrichment worker failed for chunk {i}: {e}")
                        resultados[i] = chunks[i][0]

    # A ordem importa: `add_chunks` casa texts[i] com enriched_texts[i].
    return [(resultados[i] or chunks[i][0], chunks[i][1]) for i in range(len(chunks))]
