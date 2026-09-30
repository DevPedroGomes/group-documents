import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

// O multipart passa como stream, sem `formData()` aqui: o backend conta o corpo
// enquanto le e recusa no teto. O prazo cobre o envio inteiro do arquivo, que
// numa conexao lenta passa facil do padrao de 30s.
export async function POST(request: NextRequest) {
  return proxyToBackend(request, '/upload', { streamBody: true, timeoutMs: 300_000 })
}
