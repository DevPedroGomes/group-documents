"""Defesa SSRF do crawler: 264 linhas que nao tinham nenhum teste.

Estes EXECUTAM o crawler contra servidores de verdade em 127.0.0.1, em vez de
assertar sobre o texto-fonte. O de rebinding falhava antes do IP pinado: o
conteudo interno vazava.
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.core.ingestion import url_crawler

TEXTO = (
    "Pagina publica legitima com texto suficiente para passar no filtro "
    "de tamanho minimo do extrator de conteudo."
)
SEGREDO = "CONTEUDO-INTERNO-QUE-NAO-DEVIA-VAZAR"
_real_getaddrinfo = socket.getaddrinfo


def _servidor(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class _Normal(BaseHTTPRequestHandler):
    def do_GET(self):
        c = f"<html><head><title>T</title></head><body><p>{TEXTO}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(c)))
        self.end_headers()
        self.wfile.write(c)

    def log_message(self, *a):
        pass


class _Interno(BaseHTTPRequestHandler):
    def do_GET(self):
        c = f"<html><body><p>{SEGREDO} {'x' * 60}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(c)))
        self.end_headers()
        self.wfile.write(c)

    def log_message(self, *a):
        pass


class _Gigante(BaseHTTPRequestHandler):
    """Anuncia pouco e manda muito — o caso que enchia a RAM."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        bloco = b"<p>" + b"x" * 65536 + b"</p>"
        try:
            for _ in range(200):  # ~13 MB, acima do teto de 5
                self.wfile.write(bloco)
        except Exception:
            pass  # a conexao ser cortada e exatamente o esperado

    def log_message(self, *a):
        pass


def _dns(host_alvo: str, ips: list[str]):
    """Resolve `host_alvo` para cada IP da lista, em ordem, a cada chamada."""
    seq = list(ips)
    contador = {"n": 0}

    def fake(host, port, *a, **kw):
        if host == host_alvo:
            ip = seq[min(contador["n"], len(seq) - 1)]
            contador["n"] += 1
            if ip == "127.0.0.1":
                return _real_getaddrinfo(ip, port, *a, **kw)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
        return _real_getaddrinfo(host, port, *a, **kw)

    return fake, contador


@pytest.fixture(autouse=True)
def timeout_curto(monkeypatch):
    """O teste de rebinding conecta num IP que nao responde, e esperar os 20s
    de producao para cada caso deixaria a suite lenta a toa."""
    monkeypatch.setattr(url_crawler, "FETCH_TIMEOUT", 2.0)


@pytest.fixture
def sem_checagem_de_ip(monkeypatch):
    """Isola o caminho de CONEXAO da checagem de faixa de IP.

    Os servidores de teste vivem em 127.0.0.1, que a defesa bloqueia — e deve
    bloquear. Para exercitar o resto do caminho, so esta checagem sai.
    """
    monkeypatch.setattr(url_crawler, "_check_ip", lambda ip: (True, ""))


def test_url_legitima_continua_sendo_buscada(monkeypatch, sem_checagem_de_ip):
    """Nao-regressao: pinar o IP nao pode quebrar o caminho normal."""
    srv, porta = _servidor(_Normal)
    fake, contador = _dns("ok.example", ["127.0.0.1"])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    try:
        texto, titulo = url_crawler.fetch_and_extract(f"http://ok.example:{porta}/")
    finally:
        srv.shutdown()

    assert TEXTO[:40] in texto
    assert titulo == "T"
    assert contador["n"] == 1, "o hostname foi resolvido mais de uma vez"


def test_dns_rebinding_nao_alcanca_o_alvo_interno(monkeypatch):
    """A prova que falhava antes do IP pinado.

    Um DNS hostil com TTL 0 responde publico na 1a resolucao (que a validacao
    ve) e interno na 2a (que a conexao usaria). Com o IP pinado ha UMA
    resolucao so, e nao sobra janela entre validar e conectar.
    """
    srv, porta = _servidor(_Interno)
    fake, contador = _dns("mau.example", ["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    try:
        with pytest.raises(ValueError):
            texto, _ = url_crawler.fetch_and_extract(f"http://mau.example:{porta}/")
            assert SEGREDO not in texto, "o conteudo interno vazou"
    finally:
        srv.shutdown()

    assert contador["n"] == 1, (
        f"o host foi resolvido {contador['n']} vezes: se a conexao resolve de "
        f"novo depois da validacao, a janela de rebinding voltou"
    )


def test_corpo_gigante_e_cortado_durante_o_download(monkeypatch, sem_checagem_de_ip):
    """O teto era conferido DEPOIS de baixar tudo.

    Um servidor hostil anunciando pouco e mandando muito enchia a memoria antes
    de a checagem acontecer.
    """
    srv, porta = _servidor(_Gigante)
    fake, _ = _dns("gig.example", ["127.0.0.1"])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    try:
        with pytest.raises(ValueError, match="too large"):
            url_crawler.fetch_and_extract(f"http://gig.example:{porta}/")
    finally:
        srv.shutdown()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://2130706433/",          # 127.0.0.1 em decimal
        "file:///etc/passwd",
        "http://metadata.google.internal/",
    ],
)
def test_alvos_classicos_de_ssrf_sao_barrados(url):
    ok, err = url_crawler.is_safe_url(url)
    assert not ok, f"{url} passou na validacao: {err}"
