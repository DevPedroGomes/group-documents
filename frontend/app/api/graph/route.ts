import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

// O grafo do acervo, montado a partir da trilha: documento dimensionado por
// quantas vezes foi usado numa resposta, com aresta USOU e aresta DIVERGE.
export async function GET(request: NextRequest) {
  return proxyToBackend(request, '/graph', { method: 'GET', includeQuery: true })
}
