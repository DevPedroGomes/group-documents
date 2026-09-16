"""Fixtures de PDF geradas em memoria.

Geradas em vez de versionadas de proposito: um .pdf binario no repo nao diz o
que testa, ninguem revisa o diff dele, e quando o teste quebra nao da para ver
o que mudou. Aqui o conteudo de cada caso esta escrito em Python, legivel no
code review.
"""

import io

import pymupdf


def pdf_com_texto(paginas: list[str]) -> bytes:
    """PDF textual de verdade, uma pagina por item."""
    doc = pymupdf.open()
    for conteudo in paginas:
        page = doc.new_page()
        page.insert_text((72, 72), conteudo, fontsize=11)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def pdf_escaneado(n_paginas: int = 1) -> bytes:
    """Paginas SEM camada de texto — o caso do documento digitalizado.

    E o caminho que manda a pagina para o modelo de visao, e o que nao tem teto
    de paginas: cada uma vira uma chamada paga, em serie.
    """
    doc = pymupdf.open()
    for _ in range(n_paginas):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 300, 200), color=(0, 0, 0), width=2)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def pdf_sem_pontuacao(palavras: int = 3000) -> bytes:
    """Uma pagina que e UMA sentenca — tabela, balanco, contrato em caixa alta.

    Era o caso que virava um chunk de tamanho ilimitado antes do teto.
    """
    return pdf_com_texto([" ".join(["PALAVRA"] * palavras)])


def pdf_sem_texto_algum() -> bytes:
    """PDF valido com uma pagina em branco.

    Zero paginas nao da: o pymupdf recusa salvar. Uma pagina em branco e o caso
    real equivalente — o arquivo abre, e nao ha nada para extrair.
    """
    doc = pymupdf.open()
    doc.new_page()
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def pdf_corrompido() -> bytes:
    """Cabecalho de PDF seguido de lixo: o que chega quando um upload trunca."""
    return b"%PDF-1.7\n" + b"\x00\xff" * 500
