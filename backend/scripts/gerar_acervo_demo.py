"""Gera o acervo de demonstração da Aurora Coffee Roasters.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
A demo do BrainHub não impressiona ninguém com três PDFs, porque qualquer pessoa
sobe três PDFs no chat de um provedor e tem a mesma coisa com uma experiência
melhor. O que um chat de provedor NÃO consegue é acervo: centenas de documentos
indexados, com versões que se contradizem e com vigência no tempo.

Então o acervo não é enfeite da demo. É a demo.

O QUE ELE PLANTA DE PROPÓSITO
-----------------------------
Cada política existe em DUAS versões, com números diferentes, e os chamados de
suporte antigos citam os números ANTIGOS como se fossem corretos. Isso monta a
armadilha que separa um RAG de brinquedo de um que se defende:

  - o RAG ingênuo recupera um chamado de 2024 dizendo "frete grátis acima de 120",
    responde 120 com confiança, e está errado desde junho de 2025;
  - o RAG com checagem de divergência recupera as duas políticas, percebe que elas
    respondem diferente para a mesma pergunta, e AVISA;
  - com recorte no tempo (`as_of`), "e em março de 2025?" devolve 120, e é a
    resposta certa para aquela data.

Determinístico de propósito: mesma semente, mesmo acervo. Um acervo que muda a
cada execução torna impossível escrever eval em cima dele.

USO
    python -m scripts.gerar_acervo_demo --saida /tmp/acervo --quantidade 500
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# As contradições. Esta tabela é o coração da demo.
# ---------------------------------------------------------------------------
# `desde` é a data em que a versão passou a valer. A versão 1 vale de `desde` até
# o dia anterior ao `desde` da versão 2. É isso que torna a pergunta "e em março
# de 2025?" respondível, e é o que nenhum chat de provedor sabe sobre o seu
# acervo.


@dataclass(frozen=True)
class Regra:
    chave: str
    pergunta: str
    """A pergunta em linguagem natural que esta regra responde. Vira caso de eval."""
    v1_desde: date
    v1_valor: str
    v2_desde: date
    v2_valor: str
    unidade: str = ""

    def valor_em(self, quando: date) -> str:
        return self.v2_valor if quando >= self.v2_desde else self.v1_valor


REGRAS: tuple[Regra, ...] = (
    Regra(
        chave="frete_gratis_minimo",
        pergunta="A partir de quanto o frete nacional é grátis?",
        v1_desde=date(2023, 3, 1), v1_valor="120",
        v2_desde=date(2025, 6, 1), v2_valor="150",
        unidade="reais",
    ),
    Regra(
        chave="devolucao_lacrado_dias",
        pergunta="Em quantos dias posso devolver um pacote lacrado?",
        v1_desde=date(2023, 3, 1), v1_valor="30",
        v2_desde=date(2025, 11, 1), v2_valor="21",
        unidade="dias",
    ),
    Regra(
        chave="desconto_assinante",
        pergunta="Qual é o desconto de quem assina?",
        v1_desde=date(2023, 3, 1), v1_valor="15",
        v2_desde=date(2025, 6, 1), v2_valor="12",
        unidade="por cento",
    ),
    Regra(
        chave="corte_torra_no_dia",
        pergunta="Até que horas o pedido é torrado no mesmo dia?",
        v1_desde=date(2023, 3, 1), v1_valor="14h",
        v2_desde=date(2024, 9, 1), v2_valor="15h",
    ),
    Regra(
        chave="atacado_minimo_kg",
        pergunta="A partir de quantos quilos por mês vale o preço de atacado?",
        v1_desde=date(2023, 3, 1), v1_valor="10",
        v2_desde=date(2025, 6, 1), v2_valor="8",
        unidade="quilos por mês",
    ),
)

ORIGENS = [
    ("Chapada Diamantina", "Bahia", "natural", "cacau, castanha, corpo denso"),
    ("Sul de Minas", "Minas Gerais", "cereja descascado", "caramelo, amêndoa, doçura longa"),
    ("Mogiana", "São Paulo", "natural", "chocolate ao leite, laranja madura"),
    ("Cerrado Mineiro", "Minas Gerais", "despolpado", "nozes, baunilha, acidez baixa"),
    ("Matas de Minas", "Minas Gerais", "natural", "frutas amarelas, mel"),
    ("Planalto de Vitória da Conquista", "Bahia", "cereja descascado", "cana, avelã"),
    ("Norte Pioneiro", "Paraná", "natural", "uva passa, cacau amargo"),
    ("Espírito Santo das Montanhas", "Espírito Santo", "despolpado", "pêssego, floral leve"),
]

SETORES = ["Torrefação", "Logística", "Atendimento", "Comercial", "Qualidade", "Financeiro"]

ASSUNTOS_CHAMADO = [
    "pedido atrasou na transportadora",
    "cliente quer trocar a torra da assinatura",
    "pacote chegou com a válvula danificada",
    "cobrança duplicada no cartão",
    "cliente pediu nota fiscal em outro CNPJ",
    "moagem veio diferente da pedida",
    "cliente quer cancelar antes da renovação",
    "entrega em endereço comercial fora do horário",
    "café com data de torra antiga",
    "pedido internacional retido na alfândega",
]


@dataclass
class Documento:
    nome_arquivo: str
    titulo: str
    texto: str
    emitido_em: date
    categoria: str
    # Quais regras este documento cita, e com qual valor. É o gabarito do eval:
    # sem isto, medir se a resposta está certa vira leitura humana.
    regras_citadas: dict[str, str] = field(default_factory=dict)


def _cabecalho(titulo: str, quando: date, setor: str) -> str:
    return (
        "AURORA COFFEE ROASTERS\n"
        f"{titulo}\n"
        f"Setor: {setor}\n"
        f"Emitido em: {quando.isoformat()}\n"
        + "-" * 64 + "\n\n"
    )


def politicas() -> list[Documento]:
    """As duas versões de cada política. É daqui que sai a divergência."""
    docs: list[Documento] = []
    for versao, campo_desde, campo_valor in ((1, "v1_desde", "v1_valor"), (2, "v2_desde", "v2_valor")):
        quando = max(getattr(r, campo_desde) for r in REGRAS)
        citadas = {r.chave: getattr(r, campo_valor) for r in REGRAS}
        corpo = _cabecalho(f"Política Comercial, versão {versao}", quando, "Comercial")
        corpo += (
            f"Esta versão entra em vigor em {quando.isoformat()} e substitui qualquer\n"
            "orientação anterior sobre os pontos abaixo.\n\n"
        )
        for i, r in enumerate(REGRAS, start=1):
            valor = getattr(r, campo_valor)
            unidade = f" {r.unidade}" if r.unidade else ""
            corpo += f"{i}. {r.pergunta}\n   Resposta: {valor}{unidade}.\n\n"
        corpo += (
            "Dúvidas de interpretação devem ser encaminhadas ao Comercial antes de\n"
            "serem respondidas ao cliente.\n"
        )
        docs.append(
            Documento(
                nome_arquivo=f"politica-comercial-v{versao}.txt",
                titulo=f"Política Comercial da Aurora, versão {versao}",
                texto=corpo,
                emitido_em=quando,
                categoria="politica",
                regras_citadas=citadas,
            )
        )
    return docs


def chamados(rng: random.Random, quantidade: int) -> list[Documento]:
    """Chamados resolvidos, cada um congelado na política vigente à época.

    É a armadilha: um chamado de 2024 responde "120 reais" e estava CERTO em 2024.
    Quem recupera só por semelhança acha esse chamado e responde 120 hoje.
    """
    docs: list[Documento] = []
    inicio, fim = date(2023, 4, 1), date(2026, 8, 31)
    intervalo = (fim - inicio).days
    for n in range(quantidade):
        quando = inicio + timedelta(days=rng.randrange(intervalo))
        assunto = rng.choice(ASSUNTOS_CHAMADO)
        regra = rng.choice(REGRAS)
        valor = regra.valor_em(quando)
        unidade = f" {regra.unidade}" if regra.unidade else ""
        corpo = _cabecalho(f"Chamado {2000 + n}", quando, "Atendimento")
        corpo += (
            f"Assunto: {assunto}.\n\n"
            f"O cliente perguntou: {regra.pergunta}\n"
            f"Resposta dada pelo atendimento na data: {valor}{unidade}.\n\n"
            "Encaminhamento: caso encerrado com o cliente de acordo.\n"
        )
        docs.append(
            Documento(
                nome_arquivo=f"chamado-{2000 + n}.txt",
                titulo=f"Chamado {2000 + n}: {assunto}",
                texto=corpo,
                emitido_em=quando,
                categoria="chamado",
                regras_citadas={regra.chave: valor},
            )
        )
    return docs


def atas(rng: random.Random, quantidade: int) -> list[Documento]:
    docs = []
    inicio, fim = date(2023, 4, 1), date(2026, 8, 31)
    intervalo = (fim - inicio).days
    for n in range(quantidade):
        quando = inicio + timedelta(days=rng.randrange(intervalo))
        setor = rng.choice(SETORES)
        corpo = _cabecalho(f"Ata de reunião {n + 1:03d}", quando, setor)
        corpo += (
            f"Presentes: coordenação de {setor} e representante do Comercial.\n\n"
            f"1. Revisão dos indicadores do mês anterior no setor de {setor}.\n"
            "2. Fila de torra: sem atraso relevante no período.\n"
            f"3. {rng.choice(['Ajuste de escala', 'Compra de insumo', 'Treinamento de equipe', 'Revisão de fornecedor'])} aprovado.\n"
            "4. Nada mais havendo a tratar, a reunião foi encerrada.\n"
        )
        docs.append(Documento(f"ata-{n + 1:03d}.txt", f"Ata de reunião {n + 1:03d}, {setor}", corpo, quando, "ata"))
    return docs


def fichas_de_origem(rng: random.Random, quantidade: int) -> list[Documento]:
    docs = []
    for n in range(quantidade):
        regiao, uf, processo, notas = ORIGENS[n % len(ORIGENS)]
        safra = 2023 + (n % 4)
        quando = date(safra, rng.randrange(6, 10), rng.randrange(1, 28))
        corpo = _cabecalho(f"Ficha de origem {n + 1:03d}", quando, "Qualidade")
        corpo += (
            f"Região: {regiao} ({uf})\nSafra: {safra}\nProcesso: {processo}\n"
            f"Notas sensoriais: {notas}\n"
            f"Altitude média: {rng.randrange(700, 1400)} metros\n"
            f"Pontuação SCA: {rng.randrange(82, 89)}\n\n"
            "Recomendação de torra: média para filtrado, média escura para espresso.\n"
        )
        docs.append(Documento(f"origem-{n + 1:03d}.txt", f"Ficha de origem: {regiao}, safra {safra}", corpo, quando, "origem"))
    return docs


def procedimentos(rng: random.Random, quantidade: int) -> list[Documento]:
    docs = []
    for n in range(quantidade):
        setor = rng.choice(SETORES)
        quando = date(2023 + (n % 4), rng.randrange(1, 13), rng.randrange(1, 28))
        corpo = _cabecalho(f"Procedimento operacional {n + 1:03d}", quando, setor)
        corpo += (
            f"Objetivo: padronizar a rotina de {setor.lower()} da Aurora.\n\n"
            "1. Conferir a fila do dia antes das 9h.\n"
            "2. Registrar qualquer desvio no sistema, com foto quando aplicável.\n"
            "3. Escalar para a coordenação o que não for resolvido no mesmo turno.\n"
            "4. Encerrar o turno com a fila conferida e assinada.\n"
        )
        docs.append(Documento(f"procedimento-{n + 1:03d}.txt", f"Procedimento operacional {n + 1:03d}, {setor}", corpo, quando, "procedimento"))
    return docs


def gerar(quantidade: int, semente: int = 7) -> list[Documento]:
    """Monta o acervo inteiro. As políticas entram sempre, o resto preenche."""
    rng = random.Random(semente)
    docs = politicas()
    restante = max(0, quantidade - len(docs))
    # A proporção importa: chamados são a maioria porque são a armadilha, e um
    # acervo real de suporte é mesmo dominado por histórico de atendimento.
    n_chamados = int(restante * 0.40)
    n_atas = int(restante * 0.25)
    n_origens = int(restante * 0.15)
    n_proc = restante - n_chamados - n_atas - n_origens
    docs += chamados(rng, n_chamados)
    docs += atas(rng, n_atas)
    docs += fichas_de_origem(rng, n_origens)
    docs += procedimentos(rng, n_proc)
    return docs


def _escrever_pdf(texto: str, destino: Path) -> None:
    """Escreve o documento como PDF de texto puro.

    O acervo precisa ser PDF, e nao .txt, por uma razao do proprio app: o
    `/upload` REJEITA `text/plain` de proposito, porque texto puro e reservado
    para o conteudo extraido de URL rastreada (a procedencia fica em `meta`).
    Semear .txt seria abrir uma porta que o produto fecha.

    Alem disso, PDF exercita o caminho real de ingestao: `pypdf` extrai o texto,
    e a pagina sem camada de texto cai no caminho visual. Um acervo de .txt
    provaria menos do que ele parece provar.
    """
    import pymupdf  # importado aqui: so o caminho de PDF depende dele

    doc = pymupdf.open()
    largura, altura = 595, 842  # A4 em pontos
    margem, tamanho, entrelinha = 56, 10, 14
    por_pagina = int((altura - 2 * margem) / entrelinha)

    linhas: list[str] = []
    for bruta in texto.split("\n"):
        # quebra manual: o writer de PDF nao reflui, entao linha longa sairia
        # cortada na margem e o texto extraido depois viria truncado.
        if len(bruta) <= 92:
            linhas.append(bruta)
            continue
        atual = ""
        for palavra in bruta.split(" "):
            if len(atual) + len(palavra) + 1 > 92:
                linhas.append(atual)
                atual = palavra
            else:
                atual = f"{atual} {palavra}".strip()
        if atual:
            linhas.append(atual)

    for inicio in range(0, max(len(linhas), 1), por_pagina):
        pagina = doc.new_page(width=largura, height=altura)
        y = margem
        for linha in linhas[inicio : inicio + por_pagina]:
            if linha:
                pagina.insert_text((margem, y), linha, fontsize=tamanho, fontname="helv")
            y += entrelinha
    doc.save(destino)
    doc.close()


def escrever(docs: list[Documento], saida: Path, formato: str = "txt") -> Path:
    """Grava o acervo em disco. `formato` e "txt" (rapido, para teste) ou "pdf".

    O semeador usa PDF; os testes usam txt, porque o que eles verificam e a
    coerencia do acervo e nao a serializacao.
    """
    if formato not in ("txt", "pdf"):
        raise ValueError(f"formato desconhecido: {formato}")

    saida.mkdir(parents=True, exist_ok=True)
    for d in docs:
        destino = saida / d.nome_arquivo
        if formato == "pdf":
            destino = destino.with_suffix(".pdf")
            _escrever_pdf(d.texto, destino)
        else:
            destino.write_text(d.texto, encoding="utf-8")

    # O manifesto é o que permite semear e avaliar sem reprocessar texto: ele
    # carrega o gabarito (que regra cada documento cita, com que valor).
    manifesto = {
        "gerado_por": "scripts/gerar_acervo_demo.py",
        "total": len(docs),
        "regras": [
            {
                "chave": r.chave,
                "pergunta": r.pergunta,
                "v1": {"desde": r.v1_desde.isoformat(), "valor": r.v1_valor},
                "v2": {"desde": r.v2_desde.isoformat(), "valor": r.v2_valor},
                "unidade": r.unidade,
            }
            for r in REGRAS
        ],
        "documentos": [
            {
                "arquivo": d.nome_arquivo if formato == "txt" else str(Path(d.nome_arquivo).with_suffix(".pdf")),
                "titulo": d.titulo,
                "emitido_em": d.emitido_em.isoformat(),
                "categoria": d.categoria,
                "regras_citadas": d.regras_citadas,
            }
            for d in docs
        ],
    }
    caminho = saida / "manifesto.json"
    caminho.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    return caminho


def main() -> None:
    p = argparse.ArgumentParser(description="Gera o acervo de demonstração da Aurora Coffee Roasters.")
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--quantidade", type=int, default=500)
    p.add_argument("--semente", type=int, default=7)
    p.add_argument("--formato", choices=("txt", "pdf"), default="pdf",
                   help="pdf e o formato do acervo de verdade; txt e para inspecao rapida")
    args = p.parse_args()

    docs = gerar(args.quantidade, args.semente)
    manifesto = escrever(docs, args.saida, args.formato)
    por_categoria: dict[str, int] = {}
    for d in docs:
        por_categoria[d.categoria] = por_categoria.get(d.categoria, 0) + 1
    print(f"{len(docs)} documentos em {args.saida}")
    for cat, n in sorted(por_categoria.items()):
        print(f"  {cat:14} {n}")
    print(f"manifesto: {manifesto}")


if __name__ == "__main__":
    main()
