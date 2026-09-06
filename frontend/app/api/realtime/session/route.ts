import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

// Cunha a credencial efemera da conversa por voz. O navegador nunca ve a chave
// real da OpenAI: o que sai daqui e um `ek_...` de vida curta, ja amarrado as
// instrucoes e a tool no backend.
export async function POST(request: NextRequest) {
  return proxyToBackend(request, '/realtime/session')
}
