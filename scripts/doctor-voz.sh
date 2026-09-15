#!/usr/bin/env bash
# Diagnostico da conversa por voz, em camadas: cada uma so roda se a anterior
# passou, e a saida diz exatamente onde parou e o que fazer.
#
# Uso:
#   OPENAI_API_KEY=sk-... ./scripts/doctor-voz.sh
#   ./scripts/doctor-voz.sh --local          # testa contra localhost:8000
#
# Nao imprime chave, JWT nem segredo nenhum.
set -uo pipefail

API="${API:-https://group-documents-api.pgdev.com.br}"
[ "${1:-}" = "--local" ] && API="http://localhost:8000"
MODELO_ESPERADO="${MODELO_ESPERADO:-gpt-realtime-2.1}"

verde()   { printf '\033[32m  OK\033[0m  %s\n' "$1"; }
vermelho(){ printf '\033[31m FALHA\033[0m %s\n' "$1"; }
amarelo() { printf '\033[33m AVISO\033[0m %s\n' "$1"; }
titulo()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- CAMADA 1
titulo "CAMADA 1 — a chave da OpenAI"
if [ -z "${OPENAI_API_KEY:-}" ]; then
  amarelo "OPENAI_API_KEY nao esta no ambiente desta shell — pulando."
  echo "        Rode:  OPENAI_API_KEY=sk-... $0"
  echo "        (a chave do .env da VPS e outra coisa; esta camada testa a SUA)"
else
  COD=$(curl -s -o /tmp/_om.json -w '%{http_code}' https://api.openai.com/v1/models \
        -H "Authorization: Bearer $OPENAI_API_KEY")
  case "$COD" in
    200)
      verde "a chave autentica (HTTP 200)"
      if grep -q "\"$MODELO_ESPERADO\"" /tmp/_om.json; then
        verde "o modelo $MODELO_ESPERADO esta disponivel para esta conta"
      else
        vermelho "$MODELO_ESPERADO NAO aparece na lista de modelos desta conta"
        echo "        modelos realtime que aparecem:"
        grep -o '"gpt-realtime[^"]*"' /tmp/_om.json | sort -u | sed 's/^/          /'
      fi
      ;;
    401) vermelho "chave invalida ou revogada (401)" ;;
    429)
      vermelho "429 — provavelmente sem credito"
      grep -o '"code": *"[^"]*"' /tmp/_om.json | head -1 | sed 's/^/        /'
      ;;
    *)   vermelho "resposta inesperada: HTTP $COD" ;;
  esac
  rm -f /tmp/_om.json
fi

# ---------------------------------------------------------------- CAMADA 2
titulo "CAMADA 2 — o backend cunha a credencial efemera?"
echo "  API: $API"

SAUDE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API/healthz")
if [ "$SAUDE" != "200" ]; then
  vermelho "o backend nao respondeu /healthz (HTTP $SAUDE) — parando aqui"
  exit 1
fi
verde "backend no ar"

if [ -z "${BRAINHUB_EMAIL:-}" ] || [ -z "${BRAINHUB_SENHA:-}" ]; then
  amarelo "sem BRAINHUB_EMAIL / BRAINHUB_SENHA — nao da para pegar o JWT."
  echo "        Rode:  BRAINHUB_EMAIL=... BRAINHUB_SENHA=... $0"
  echo "        Sem isto, o teste do /realtime/session fica de fora."
  exit 0
fi

JWT=$(curl -s --max-time 15 -X POST "$API/auth/login" \
      -H 'Content-Type: application/json' \
      -d "{\"email\":\"$BRAINHUB_EMAIL\",\"password\":\"$BRAINHUB_SENHA\"}" \
      | sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p')

if [ -z "$JWT" ]; then
  vermelho "login falhou — confira email e senha"
  exit 1
fi
verde "login ok (token obtido, nao sera impresso)"

RESP=$(curl -s --max-time 25 -w '\n%{http_code}' -X POST "$API/realtime/session" \
       -H "Authorization: Bearer $JWT")
COD=$(echo "$RESP" | tail -1)
CORPO=$(echo "$RESP" | sed '$d')

case "$COD" in
  200)
    if echo "$CORPO" | grep -q '"client_secret" *: *"ek_'; then
      verde "credencial efemera cunhada (ek_...) — o caminho backend esta inteiro"
      echo "$CORPO" | sed -n 's/.*"model" *: *"\([^"]*\)".*/          modelo em uso: \1/p'
    else
      vermelho "200, mas sem client_secret no formato ek_ — contrato mudou?"
    fi
    ;;
  503)
    vermelho "503 — a voz nao esta configurada ou a OpenAI recusou"
    echo "$CORPO" | sed -n 's/.*"detail" *: *"\([^"]*\)".*/          motivo: \1/p'
    echo "          'not configured' = OPENAI_API_KEY ausente no .env da VPS"
    echo "          'unavailable'    = a chave existe mas a OpenAI recusou (credito?)"
    ;;
  429) vermelho "429 — teto diario de sessoes de voz atingido (daily_realtime_limit)" ;;
  *)   vermelho "HTTP $COD inesperado" ;;
esac

# ---------------------------------------------------------------- CAMADA 3
titulo "CAMADA 3 — a conversa (manual, precisa de microfone)"
cat <<'TXT'
  Abra o app, faca login, SELECIONE ao menos um documento (o botao
  "Talk to your archive" so habilita com >= 1) e fale.

  Suba um PDF SEU antes: o acervo demo tem 11 chunks, e com ele a busca
  volta vazia — voce nao consegue distinguir "o RAG nao achou" de
  "o pipeline quebrou".

  O que observar, na ordem dos quatro consertos:
    1. A SUA fala aparece no painel?        -> transcricao da entrada ligada
    2. Ele diz "let me check" antes?        -> frase de preenchimento
    3. O silencio apos a frase e curto?     -> disparo antecipado da busca
    4. Ele corta voce quando voce pausa?    -> se sim, subir silence_duration_ms
                                               de 700 para 900 em realtime.py
TXT
echo
