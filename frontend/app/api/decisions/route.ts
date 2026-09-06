import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

// A trilha de decisao existia no backend desde a migracao 003 e NAO tinha rota
// proxy: o painel nao conseguia alcanca-la, entao a feature mais diferenciada do
// projeto estava construida, testada e invisivel. Ver pgtech/docs/24 §7.
export async function GET(request: NextRequest) {
  return proxyToBackend(request, '/decisions', { method: 'GET', includeQuery: true })
}
