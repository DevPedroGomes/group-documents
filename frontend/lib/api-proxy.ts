/**
 * Proxy das rotas /api/* do Next para o backend.
 *
 * Tres coisas que as rotas faziam cada uma do seu jeito, e erravam:
 * 1. Chamavam a URL PUBLICA do backend. Pela internet, todo visitante chegava
 *    ao rate limit com o IP deste container, entao dividiam um balde so. Agora
 *    o destino e a rede interna (`API_INTERNAL_URL`) e o IP do visitante vai no
 *    `X-Forwarded-For`, lido do `x-real-ip` que o Traefik grava.
 * 2. Nao tinham timeout: backend pendurado segurava o request para sempre.
 * 3. Liam `response.json()` sem guarda e rotulavam tudo como SSE ou JSON; um
 *    502 do backend virava 500 opaco. Agora o corpo passa como stream, com o
 *    content-type do backend, e falha de rede vira 502/504 com `detail`.
 */

import { isIP } from 'node:net'

type Metodo = 'GET' | 'POST' | 'PUT' | 'DELETE'

type ProxyOptions = {
  method?: Metodo
  includeQuery?: boolean
  /**
   * Prazo para o backend devolver os HEADERS. Depois deles o timer para: o
   * corpo (SSE da resposta, arquivo do preview) nunca e cortado no meio.
   */
  timeoutMs?: number
  /**
   * Repassa o corpo como stream, com o content-type e o content-length do
   * cliente (multipart do upload). Sem isto o corpo e lido como texto JSON.
   */
  streamBody?: boolean
}

const TIMEOUT_PADRAO_MS = 30_000

// Headers da resposta do backend que valem para o navegador. O resto (hop by
// hop, content-length de um corpo que o fetch pode ter descomprimido) fica.
const HEADERS_REPASSADOS = ['content-type', 'content-disposition', 'retry-after', 'cache-control']

/** URL base do backend vista do servidor Next. Vazio conta como ausente. */
export function backendBaseUrl(): string {
  const base =
    process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
  return base.replace(/\/+$/, '')
}

/**
 * O IP do visitante, do `x-real-ip` que o Traefik sobrescreve em todo request.
 * NUNCA do `x-forwarded-for` recebido: esse o cliente escreve o que quiser, e
 * trocar de IP a cada request zeraria o proprio rate limit.
 */
function ipDoVisitante(req: Request): string | null {
  const ip = req.headers.get('x-real-ip')?.trim()
  return ip && isIP(ip) ? ip : null
}

function erroJson(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: { 'cache-control': 'no-store' } })
}

export async function proxyToBackend(
  req: Request,
  endpoint: string,
  options: ProxyOptions = {}
): Promise<Response> {
  const {
    method = 'POST',
    includeQuery = false,
    timeoutMs = TIMEOUT_PADRAO_MS,
    streamBody = false,
  } = options

  const url = backendBaseUrl() + endpoint + (includeQuery ? new URL(req.url).search : '')

  const headers = new Headers()
  const token = req.headers.get('authorization')
  if (token) headers.set('authorization', token)
  const ip = ipDoVisitante(req)
  if (ip) headers.set('x-forwarded-for', ip)

  const init: RequestInit & { duplex?: 'half' } = { method, headers, cache: 'no-store' }

  if (method === 'POST' || method === 'PUT') {
    if (streamBody) {
      // O backend conta o corpo ENQUANTO le e recusa com 413 no teto. Com o
      // corpo bufferizado aqui, o arquivo inteiro passava pela memoria deste
      // container antes de o backend poder dizer nao.
      const tipo = req.headers.get('content-type')
      const tamanho = req.headers.get('content-length')
      if (tipo) headers.set('content-type', tipo)
      if (tamanho) headers.set('content-length', tamanho)
      init.body = req.body
      init.duplex = 'half'
    } else {
      headers.set('content-type', 'application/json')
      init.body = await req.text()
    }
  }

  const controle = new AbortController()
  const timer = setTimeout(() => controle.abort(), timeoutMs)
  init.signal = controle.signal

  let resposta: Response
  try {
    resposta = await fetch(url, init)
  } catch {
    return controle.signal.aborted
      ? erroJson(504, 'The server took too long to respond. Please try again.')
      : erroJson(502, 'Could not reach the server. Please try again.')
  } finally {
    clearTimeout(timer)
  }

  const saida = new Headers()
  for (const nome of HEADERS_REPASSADOS) {
    const valor = resposta.headers.get(nome)
    if (valor) saida.set(nome, valor)
  }
  if (saida.get('content-type')?.includes('text/event-stream')) {
    saida.set('cache-control', 'no-cache')
    // Qualquer proxy com buffer no caminho seguraria os tokens ate o fim.
    saida.set('x-accel-buffering', 'no')
  }

  // Corpo como stream: `response.text()` corrompe binario (o preview de PDF,
  // imagem e audio vem como arquivo) e seguraria o SSE ate o fim.
  return new Response(resposta.body, { status: resposta.status, headers: saida })
}
