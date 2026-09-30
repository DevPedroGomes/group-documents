"""A extracao de PDF, EXECUTADA.

Ate aqui `extrair_paginas` e `chunk_text` nunca rodavam em teste nenhum: a
suite asseverava o texto-fonte. Nao existia um unico fixture de PDF no repo.

Os PDFs sao gerados em memoria (tests/fixtures) em vez de versionados: um
binario no repo nao diz o que testa e ninguem revisa o diff dele.
"""

import pytest

from app.core.ingestion.chunker import _token_count, chunk_text
from app.core.ingestion.falhas import FalhaPermanente
from app.core.ingestion.pdf_processor import extrair_paginas, renderizar_paginas
from tests.fixtures import (
    pdf_com_texto,
    pdf_corrompido,
    pdf_escaneado,
    pdf_sem_pontuacao,
    pdf_sem_texto_algum,
)


# Texto longo o bastante para passar do limiar: abaixo dele a pagina e tratada
# como escaneada, mesmo tendo texto. Ver o teste do limiar mais abaixo.
_LONGO_1 = "Primeira pagina do relatorio. " * 8
_LONGO_2 = "Segunda pagina do relatorio. " * 8


def test_pdf_textual_vira_uma_pagina_por_pagina():
    paginas = extrair_paginas(pdf_com_texto([_LONGO_1, _LONGO_2]))

    assert len(paginas) == 2
    assert paginas[0].numero == 1 and paginas[1].numero == 2
    assert "Primeira" in paginas[0].texto
    assert "Segunda" in paginas[1].texto
    assert not paginas[0].escaneada


def test_pdf_escaneado_vai_para_o_caminho_visual():
    """Pagina sem camada de texto e renderizada para o modelo de visao.

    Nao existe OCR no projeto — nenhum tesseract, easyocr ou paddleocr. O
    caminho visual e o unico, e ele CUSTA: uma chamada paga por pagina, em
    serie e sem teto. Um PDF escaneado grande estoura o job_timeout, vira
    `failed`, retenta do zero e queima o custo de novo.
    """
    dados = pdf_escaneado(2)
    paginas = extrair_paginas(dados)

    assert len(paginas) == 2
    for p in paginas:
        assert p.texto == "", "pagina escaneada nao deveria ter texto extraido"
        assert p.escaneada, "pagina sem texto deveria ir para o caminho visual"
    renderizadas = list(renderizar_paginas(dados, [p.numero for p in paginas], dpi=50))
    assert [n for n, _ in renderizadas] == [1, 2]
    assert all(imagem is not None for _, imagem in renderizadas), "a pagina precisa ser renderizada"


def test_render_das_escaneadas_e_uma_pagina_por_vez(monkeypatch):
    """Antes todas as escaneadas viravam imagem juntas, antes de qualquer uma
    ser processada: 300 paginas a 150 DPI sao ~1,9 GB de RAM no worker."""
    import pymupdf

    renderizadas: list[int] = []
    original = pymupdf.Page.get_pixmap

    def espiao(self, *a, **k):
        renderizadas.append(self.number + 1)
        return original(self, *a, **k)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", espiao)
    gerador = renderizar_paginas(pdf_escaneado(3), [1, 2, 3], dpi=50)

    numero, imagem = next(gerador)
    assert numero == 1 and imagem is not None
    assert renderizadas == [1], "renderizou pagina que ninguem pediu ainda"
    assert [n for n, _ in gerador] == [2, 3]
    assert renderizadas == [1, 2, 3]


def test_pdf_acima_do_teto_de_paginas_falha_como_permanente(monkeypatch):
    """Sem teto, um PDF de milhares de paginas estourava o job de 30 minutos,
    virava `failed`, retentava do zero e queimava o custo de novo."""
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "max_pdf_pages", 2)

    with pytest.raises(FalhaPermanente) as falha:
        extrair_paginas(pdf_com_texto([_LONGO_1, _LONGO_2, _LONGO_1]))

    assert falha.value.mensagem == "The PDF has more pages than the limit (2)."
    assert len(extrair_paginas(pdf_com_texto([_LONGO_1, _LONGO_2]))) == 2


def test_pdf_sem_texto_nao_inventa_conteudo():
    """Pagina em branco: o arquivo abre e nao ha nada. Nao pode virar texto."""
    paginas = extrair_paginas(pdf_sem_texto_algum())

    assert len(paginas) == 1
    assert paginas[0].texto == ""


def test_pdf_corrompido_falha_de_forma_limpa():
    """Upload truncado nao pode virar uma pagina de lixo indexada.

    O contrato e levantar: o envelope do worker converte isso em
    status='failed' com a causa gravada.
    """
    with pytest.raises(Exception):
        extrair_paginas(pdf_corrompido())


def test_pagina_sem_pontuacao_nao_vira_chunk_ilimitado():
    """O bug que so aparece com um PDF de verdade na mao.

    Uma pagina de tabela ou de contrato em caixa alta e UMA sentenca: o laco de
    chunk_text so fecha um chunk quando ja ha algo acumulado, entao ela entrava
    inteira. Agora ha teto duro.
    """
    paginas = extrair_paginas(pdf_sem_pontuacao(3000))
    assert len(paginas) == 1

    teto = 500
    chunks = chunk_text(paginas[0].texto, max_tokens=teto)

    assert len(chunks) > 1, "a pagina saiu num chunk so"
    assert max(_token_count(c) for c in chunks) <= teto


def test_o_texto_extraido_perde_o_whitespace_redundante():
    paginas = extrair_paginas(pdf_com_texto(["Texto    com     espacos."]))
    assert "  " not in paginas[0].texto


def test_a_extracao_preserva_a_quebra_de_linha_de_uma_tabela():
    """Colapsar todo whitespace juntava as linhas da tabela numa so, e cada
    valor perdia o rotulo da propria linha."""
    tabela = "Plano Basico    10 GB    R$ 50\nPlano Pro    50 GB    R$ 120\nPlano Max    200 GB    R$ 300"

    (pagina,) = extrair_paginas(pdf_com_texto([tabela]))

    assert pagina.texto.splitlines() == [
        "Plano Basico 10 GB R$ 50", "Plano Pro 50 GB R$ 120", "Plano Max 200 GB R$ 300",
    ]


def test_tabela_grande_demais_e_partida_por_linha_e_mantem_as_linhas():
    """Tabela sem pontuacao e UMA sentenca; partida por palavra, as linhas se
    misturavam. Partida por linha, cada pedaco fica abaixo do teto e cada linha
    inteira."""
    linhas = [f"Item {i} codigo {i * 7} quantidade {i % 13} valor {i * 3} reais" for i in range(300)]

    chunks = chunk_text("\n".join(linhas), max_tokens=200)

    assert len(chunks) > 1
    assert max(_token_count(c) for c in chunks) <= 200
    for c in chunks:
        assert all(linha in linhas for linha in c.splitlines()), "uma linha da tabela foi partida"


@pytest.mark.parametrize("n", [1, 3])
def test_numeracao_de_pagina_e_1_based_e_contigua(n):
    """A numeracao vai para a trilha de decisao e para a citacao falada.

    Se escorregar, o agente cita a pagina errada — um erro que ninguem percebe
    olhando a resposta, so conferindo o documento.
    """
    paginas = extrair_paginas(pdf_com_texto([f"Pagina {i}." for i in range(1, n + 1)]))
    assert [p.numero for p in paginas] == list(range(1, n + 1))


def test_pagina_curta_com_texto_e_tratada_como_escaneada():
    """Descoberto ao escrever estas fixtures, e custa dinheiro.

    O criterio de "pagina escaneada" e ter MENOS de
    `pdf_min_chars_por_pagina` (120) caracteres — nao e a ausencia de texto.
    Entao uma pagina de rosto, uma pagina so com titulo ou uma pagina final com
    "Obrigado" e renderizada e mandada para o modelo de VISAO, que e uma
    chamada paga, em serie e sem teto.

    Num PDF com muitas paginas curtas isso multiplica o custo da ingestao sem
    que ninguem perceba: o texto estava la o tempo todo.

    O teste nao julga o limiar — ele prende o comportamento, para que mexer no
    valor seja uma decisao consciente e nao um efeito colateral.
    """
    from app.config.settings import get_settings

    limiar = get_settings().pdf_min_chars_por_pagina
    curta = "Capitulo 1"
    assert len(curta) < limiar

    paginas = extrair_paginas(pdf_com_texto([curta]))

    assert paginas[0].texto == curta, "o texto foi extraido normalmente"
    assert paginas[0].escaneada, (
        f"pagina com {len(curta)} chars (< {limiar}) vai para o caminho visual"
    )
