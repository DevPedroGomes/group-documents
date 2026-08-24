FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# `git` entra e sai no MESMO layer: o pip precisa dele para resolver a
# dependencia `git+https://` do agent-ops, mas um binario git na imagem de
# runtime e superficie de ataque que nada usa. Precisa ser um `RUN` so —
# instalar num layer e purgar noutro nao reduz a imagem, porque o layer
# anterior ja gravou o binario e layers sao imutaveis.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN groupadd -r app -g 10001 && useradd -r -u 10001 -g app -d /app -s /sbin/nologin app && \
    mkdir -p /app/uploads /tmp/pycache && \
    chown -R app:app /app /tmp/pycache

ENV PYTHONPYCACHEPREFIX=/tmp/pycache

USER app

EXPOSE 8000

# --forwarded-allow-ips: sem isto o uvicorn nao confia no X-Forwarded-For que o
# Traefik seta, e `request.client.host` vira o IP do PROPRIO Traefik. O
# resultado e que o slowapi chaveava TODOS os visitantes no mesmo balde: um
# unico "30/minute" para o mundo inteiro, e nenhum limite por pessoa. A porta
# 8000 nao e publicada no host — o container so e alcancavel pela rede Docker,
# via Traefik — entao confiar no proxy aqui e seguro.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
