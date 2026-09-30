"""O `.env.example` e o que alguem copia para subir o app, entao ele tem de bater
com o que o codigo le.

Ele ja fixou um modelo retirado (`claude-sonnet-4-20250514`), um modelo de
embedding so de texto que quebra a ingestao multimodal, envs que nada le
(Google, Langfuse) e deixou de fora OPENAI_API_KEY, DEEPGRAM_API_KEY,
LLM_PROVIDER e os tetos diarios.
"""

from pathlib import Path

ARQUIVO = Path(__file__).resolve().parents[1] / ".env.example"


def _envs() -> dict[str, str]:
    linhas = [l.strip() for l in ARQUIVO.read_text("utf-8").splitlines()]
    return dict(l.split("=", 1) for l in linhas if l and not l.startswith("#"))


def test_declara_toda_env_que_o_app_e_o_agent_ops_leem_e_so_elas():
    from agent_ops.config import Config

    from app.config.settings import Settings

    lidas = {nome.upper() for nome in Settings.model_fields}
    lidas |= {f"AGENT_OPS_{nome.upper()}" for nome in Config.model_fields}

    declaradas = set(_envs())
    assert lidas - declaradas == set(), "env lida pelo codigo e ausente do exemplo"
    assert declaradas - lidas == set(), "env no exemplo que nada le"


def test_os_modelos_do_exemplo_sao_os_do_codigo():
    from app.config.settings import Settings

    envs = _envs()
    for nome in ("generation_model", "fast_model", "voyage_doc_model", "cohere_rerank_model",
                 "realtime_model", "realtime_transcribe_model", "deepgram_model", "embedding_dimensions"):
        assert envs[nome.upper()] == str(Settings.model_fields[nome].default), nome


def test_o_exemplo_carrega_como_configuracao(monkeypatch):
    from app.config.settings import Settings

    for nome in ("MAX_PDF_PAGES", "DAILY_REALTIME_TOOL_LIMIT", "ENABLE_WEB_FALLBACK"):
        monkeypatch.delenv(nome, raising=False)

    carregada = Settings(_env_file=ARQUIVO)

    assert carregada.max_pdf_pages == 300
    assert carregada.daily_realtime_tool_limit == 400
    assert carregada.enable_web_fallback is False
