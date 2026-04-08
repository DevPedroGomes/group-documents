import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const authorization = request.headers.get('Authorization') || ''

  const response = await fetch(`${API_URL}/threads/${id}/messages`, {
    headers: { 'Authorization': authorization },
  })

  const data = await response.json()
  return NextResponse.json(data, { status: response.status })
}
