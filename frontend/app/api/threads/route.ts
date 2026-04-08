import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(request: NextRequest) {
  const authorization = request.headers.get('Authorization') || ''

  const response = await fetch(`${API_URL}/threads`, {
    headers: { 'Authorization': authorization },
  })

  const data = await response.json()
  return NextResponse.json(data, { status: response.status })
}
