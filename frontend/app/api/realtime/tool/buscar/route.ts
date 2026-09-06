import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

// Executa a busca que o modelo pediu no meio da fala. Passa pelo backend de
// proposito: quem decide o que e relevante e o grader do servidor, e e la que a
// trilha de decisao e gravada. Deixar o navegador escolher os trechos seria
// entregar a ele o isolamento entre inquilinos.
export async function POST(request: NextRequest) {
  return proxyToBackend(request, '/realtime/tool/buscar')
}
