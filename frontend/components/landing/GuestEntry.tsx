'use client'

import { useEffect, useState } from 'react'
import { ArrowRight, Loader2 } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { takeGuestSessionEnded } from '@/lib/auth'

export default function GuestEntry() {
  const { loginAsGuest } = useAuth()
  const [opening, setOpening] = useState(false)
  const [error, setError] = useState('')
  const [sessionEnded, setSessionEnded] = useState(false)

  useEffect(() => {
    if (!takeGuestSessionEnded()) return
    setSessionEnded(true)
    document.getElementById('index')?.scrollIntoView({ block: 'start' })
  }, [])

  async function openSample() {
    if (opening) return
    setOpening(true)
    setError('')
    try {
      await loginAsGuest()
    } catch (cause) {
      const networkFailure = cause instanceof TypeError || (cause instanceof Error && cause.name === 'AbortError')
      setError(networkFailure
        ? 'Could not reach the demo. Please try again in a moment.'
        : cause instanceof Error ? cause.message : 'Could not open the sample library. Please try again.')
      setOpening(false)
    }
  }

  return (
    <div className="max-w-lg">
      {sessionEnded && <p role="status" className="mb-3 text-sm text-blue-100">Your sample session ended. You can start a new session if your daily allowance is available.</p>}
      <Button type="button" size="lg" onClick={openSample} disabled={opening} className="rounded-full px-6 gap-2">
        {opening ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
        {opening ? 'Opening the demo…' : 'Try without an account'}
        {!opening && <ArrowRight className="h-4 w-4" aria-hidden />}
      </Button>
      <p className="mt-3 text-sm text-neutral-400 leading-relaxed">
        Ask questions about fictional sample documents. No signup required.
      </p>
      {error && (
        <div role="alert" className="mt-3 rounded-xl border border-red-400/25 bg-red-400/10 p-3 text-sm text-red-200">
          <p>{error}</p>
          <a href="#auth" className="mt-2 inline-block underline underline-offset-4">Use an account</a>
        </div>
      )}
    </div>
  )
}
