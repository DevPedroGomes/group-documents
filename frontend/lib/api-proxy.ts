/**
 * Helper para fazer proxy de requisições para o backend
 * Elimina duplicação de código nas API routes
 */

type ProxyOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  includeQuery?: boolean
}

export async function proxyToBackend(
  req: Request,
  endpoint: string,
  options: ProxyOptions = {}
): Promise<Response> {
  const { method = 'POST', includeQuery = false } = options

  // Extrair token de autorização
  const token = req.headers.get('Authorization') || ''

  // Construir URL do backend
  const backendUrl = process.env.NEXT_PUBLIC_API_URL + endpoint

  // Adicionar query params se necessário
  const url = includeQuery
    ? backendUrl + new URL(req.url).search
    : backendUrl

  // Preparar headers
  const headers: HeadersInit = {
    Authorization: token
  }

  // Adicionar body para métodos POST/PUT
  let body: BodyInit | undefined
  if (method === 'POST' || method === 'PUT') {
    headers['content-type'] = 'application/json'
    body = await req.text()
  }

  // Fazer requisição ao backend
  const response = await fetch(url, {
    method,
    headers,
    body
  })

  // Stream the response through (supports SSE)
  const contentType = response.headers.get('content-type') || 'application/json'
  const isStream = contentType.includes('text/event-stream')

  if (isStream) {
    return new Response(response.body, {
      status: response.status,
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      },
    })
  }

  return new Response(await response.text(), {
    status: response.status,
    headers: {
      'content-type': contentType,
    },
  })
}
