import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

// O timeout do helper vale ate os headers chegarem; o stream da resposta nunca
// e cortado. O content-type vem do backend: um 422 ou 429 e JSON, e rotula-lo
// como `text/event-stream` fazia o erro chegar ao cliente como stream vazio.
export async function POST(request: NextRequest) {
  return proxyToBackend(request, '/chat')
}
