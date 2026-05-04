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

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
