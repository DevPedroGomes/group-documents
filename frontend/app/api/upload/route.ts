import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  const authorization = request.headers.get('Authorization') || ''

  // Forward the multipart form data as-is to the backend
  const formData = await request.formData()

  const response = await fetch(`${API_URL}/files/upload`, {
    method: 'POST',
    headers: {
      Authorization: authorization,
    },
    body: formData,
  })

  const data = await response.json()
  return NextResponse.json(data, { status: response.status })
}
