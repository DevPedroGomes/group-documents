FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
