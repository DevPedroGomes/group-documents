"""O acervo de demonstração é a demo, então ele precisa de teste como código.

A demo do BrainHub se apoia em três momentos que um chat de provedor não encena:
acervo grande, fontes que se contradizem, e recorte no tempo. Os três dependem de
o acervo estar montado exatamente como projetado. Se o gerador mudar e a
contradição sumir, a demo continua "funcionando" e passa a não provar nada, que é
a pior forma de quebrar.

Nenhum destes testes toca rede ou banco: o gerador é puro de propósito.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from scripts.gerar_acervo_demo import REGRAS, escrever, gerar


def test_toda_regra_muda_de_valor_entre_as_versoes():
    """Uma regra cujo valor não muda não planta contradição nenhuma.

    É o teste que impede alguém de "limpar" a tabela deixando v1 == v2 e achar
    que não quebrou nada.
    """
    for r in REGRAS:
        assert r.v1_valor != r.v2_valor, f"{r.chave} não contradiz nada"
        assert r.v2_desde > r.v1_desde, f"{r.chave} tem v2 anterior à v1"


def test_valor_em_respeita_a_data_de_virada():
    """O recorte no tempo é a feature. No dia exato da virada já vale a v2."""
    for r in REGRAS:
        assert r.valor_em(r.v1_desde) == r.v1_valor
        assert r.valor_em(r.v2_desde) == r.v2_valor, f"{r.chave} não virou no dia certo"
        um_dia_antes = date.fromordinal(r.v2_desde.toordinal() - 1)
        assert r.valor_em(um_dia_antes) == r.v1_valor


def test_as_duas_politicas_existem_e_divergem_em_tudo():
    docs = gerar(120)
    politicas = [d for d in docs if d.categoria == "politica"]
    assert len(politicas) == 2

    v1, v2 = politicas
    assert v1.regras_citadas.keys() == v2.regras_citadas.keys()
    for chave in v1.regras_citadas:
        assert v1.regras_citadas[chave] != v2.regras_citadas[chave], (
            f"as duas versões da política concordam em {chave}, então não há o que detectar"
        )


def test_chamado_cita_o_valor_vigente_na_data_dele():
    """A armadilha só funciona se o histórico for internamente coerente.

    Um chamado de 2024 dizendo "150" seria um erro do gerador, não uma pegadinha:
    ele estaria simplesmente errado para a época, e o acervo perderia a
    propriedade de "cada documento estava certo quando foi escrito".
    """
    docs = gerar(500)
    por_chave = {r.chave: r for r in REGRAS}
    chamados = [d for d in docs if d.categoria == "chamado"]
    assert chamados, "o acervo precisa de chamados: são eles que enganam o RAG ingênuo"

    for d in chamados:
        for chave, valor in d.regras_citadas.items():
            esperado = por_chave[chave].valor_em(d.emitido_em)
            assert valor == esperado, (
                f"{d.nome_arquivo} de {d.emitido_em} cita {valor} para {chave}, "
                f"mas o vigente na data era {esperado}"
            )


def test_o_historico_cita_os_dois_lados_de_cada_regra():
    """Se todos os chamados de uma regra fossem do lado novo, não haveria armadilha.

    É este teste que garante que existe material suficiente para o RAG ingênuo
    tropeçar, que é o ponto da demo.
    """
    docs = gerar(500)
    por_chave = {r.chave: r for r in REGRAS}
    antigos: dict[str, int] = {r.chave: 0 for r in REGRAS}
    novos: dict[str, int] = {r.chave: 0 for r in REGRAS}

    for d in docs:
        if d.categoria != "chamado":
            continue
        for chave, valor in d.regras_citadas.items():
            if valor == por_chave[chave].v1_valor:
                antigos[chave] += 1
            else:
                novos[chave] += 1

    for chave in por_chave:
        assert antigos[chave] >= 3, f"{chave} tem poucos chamados com o valor antigo"
        assert novos[chave] >= 3, f"{chave} tem poucos chamados com o valor novo"


def test_o_acervo_e_deterministico():
    """Eval em cima de acervo que muda a cada execução não mede nada."""
    a = gerar(300, semente=7)
    b = gerar(300, semente=7)
    assert [d.nome_arquivo for d in a] == [d.nome_arquivo for d in b]
    assert [d.texto for d in a] == [d.texto for d in b]

    c = gerar(300, semente=8)
    assert [d.texto for d in a] != [d.texto for d in c], "a semente não está sendo usada"


def test_nomes_de_arquivo_sao_unicos():
    """Nome repetido sobrescreveria silenciosamente e o acervo encolheria."""
    docs = gerar(500)
    nomes = [d.nome_arquivo for d in docs]
    assert len(nomes) == len(set(nomes))


def test_quantidade_pedida_e_respeitada():
    for n in (50, 120, 500):
        assert len(gerar(n)) == n


def test_manifesto_carrega_o_gabarito(tmp_path):
    """O manifesto é o que permite avaliar sem reler o texto dos documentos."""
    docs = gerar(200)
    caminho = escrever(docs, tmp_path)
    dados = json.loads(caminho.read_text(encoding="utf-8"))

    assert dados["total"] == 200
    assert len(dados["documentos"]) == 200
    assert {r["chave"] for r in dados["regras"]} == {r.chave for r in REGRAS}

    # todo arquivo listado no manifesto existe em disco
    for d in dados["documentos"]:
        assert (tmp_path / d["arquivo"]).is_file()


@pytest.mark.parametrize("regra", REGRAS, ids=lambda r: r.chave)
def test_cada_regra_tem_pergunta_em_linguagem_natural(regra):
    """A pergunta vira caso de eval. Sem ela a regra não é verificável."""
    assert regra.pergunta.endswith("?")
    assert len(regra.pergunta) > 20


# ---------------------------------------------------------------------------
# A conta demo nao tem senha padrao
# ---------------------------------------------------------------------------

@pytest.fixture
def semeadura(monkeypatch):
    """O script com banco e fila trocados: anota com que senha a conta seria criada."""
    import sys

    from scripts import semear_acervo_demo as script

    criadas: list[tuple[str, str]] = []
    monkeypatch.setattr(script, "garantir_usuario", lambda email, senha: criadas.append((email, senha)) or "u")
    monkeypatch.setattr(script, "limpar", lambda _uid: 0)
    monkeypatch.delenv("ACERVO_DEMO_SENHA", raising=False)

    def rodar(*argumentos):
        monkeypatch.setattr(sys, "argv", ["semear_acervo_demo", "--email", "demo@exemplo.com.br",
                                          "--quantidade", "50", *argumentos])
        script.main()

    return rodar, criadas, monkeypatch


@pytest.mark.parametrize("argumentos", [(), ("--senha", "curta-demais"), ("--senha", "")])
def test_sem_senha_explicita_e_longa_a_semeadura_nao_cria_a_conta(semeadura, argumentos):
    """A senha padrao vivia no repo publico: era a senha da demo para qualquer um."""
    rodar, criadas, _ = semeadura

    with pytest.raises(SystemExit) as saida:
        rodar("--limpar", *argumentos)

    assert "ACERVO_DEMO_SENHA" in str(saida.value)
    assert criadas == []


def test_senha_vem_do_argumento_ou_da_env(semeadura):
    rodar, criadas, monkeypatch = semeadura

    rodar("--limpar", "--senha", "uma-senha-com-mais-de-16")
    monkeypatch.setenv("ACERVO_DEMO_SENHA", "outra-senha-com-mais-de-16")
    rodar("--limpar")

    assert [senha for _, senha in criadas] == ["uma-senha-com-mais-de-16", "outra-senha-com-mais-de-16"]


def test_estimar_nao_pede_senha_porque_nao_escreve_nada(semeadura, capsys):
    from scripts import semear_acervo_demo as script

    rodar, criadas, monkeypatch = semeadura
    monkeypatch.setattr(script, "buscar_usuario", lambda _email: None)

    rodar("--estimar")

    assert criadas == []
    assert "a semear:     50" in capsys.readouterr().out
