import { NextRequest } from 'next/server'
import { proxyToBackend } from '@/lib/api-proxy'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ message_id: string }> }
) {
  const { message_id } = await params
  return proxyToBackend(request, `/decisions/${encodeURIComponent(message_id)}`, {
    method: 'GET',
  })
}
