import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

// O backend baixa a pagina antes de responder, dai o prazo maior que o padrao.
export async function POST(request: NextRequest) {
  return proxyToBackend(request, '/crawl', { timeoutMs: 60_000 })
}
