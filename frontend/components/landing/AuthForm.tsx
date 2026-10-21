'use client'

import { useEffect, useState } from 'react'
import { ArrowRight, Loader2, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { GUEST_UNAVAILABLE, useAuth } from '@/contexts/AuthContext'
import { takeGuestSessionEnded } from '@/lib/auth'

// ─── Auth Form (guest entry + login / signup tabs) ───────────────────
export default function AuthForm() {
  const { login, register, loginAsGuest } = useAuth()
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [msg, setMsg] = useState('')
  // Some quando a API diz que o modo convidado esta desligado: fica o cadastro.
  const [convidadoDisponivel, setConvidadoDisponivel] = useState(true)
  const [entrandoComoConvidado, setEntrandoComoConvidado] = useState(false)
  const [sessaoEncerrada, setSessaoEncerrada] = useState(false)

  // Quem volta de uma sessao de convidado vencida cai aqui, com o aviso a vista.
  useEffect(() => {
    if (!takeGuestSessionEnded()) return
    setSessaoEncerrada(true)
    document.getElementById('auth')?.scrollIntoView({ block: 'center' })
  }, [])

  const onGuest = async () => {
    setEntrandoComoConvidado(true)
    setStatus('idle')
    setMsg('')
    try {
      await loginAsGuest()
    } catch (error) {
      if (error instanceof Error && error.name === GUEST_UNAVAILABLE) setConvidadoDisponivel(false)
      setStatus('error')
      setMsg(error instanceof Error ? error.message : 'Could not open the sample library')
    } finally {
      setEntrandoComoConvidado(false)
    }
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setStatus('loading')
    setMsg('')

    try {
      if (mode === 'login') {
        await login(email, password)
      } else {
        await register(email, password, fullName)
      }
    } catch (error) {
      setStatus('error')
      setMsg(error instanceof Error ? error.message : 'Authentication failed')
    }
  }

  return (
    <div
      className="relative rounded-3xl bg-white/[0.04] ring-1 ring-white/10 border-gradient backdrop-blur p-6 sm:p-7"
      style={{ borderRadius: 24 }}
    >
      {sessaoEncerrada && (
        <p role="status" className="mb-5 rounded-lg bg-blue-400/10 border border-blue-400/20 px-3 py-2 text-sm text-blue-100">
          Your sample session ended.
        </p>
      )}

      {/* Entrada sem cadastro: le o acervo de demonstracao, so leitura. */}
      {convidadoDisponivel && (
        <div className="mb-6">
          <Button
            type="button"
            variant="outline"
            onClick={onGuest}
            disabled={entrandoComoConvidado || status === 'loading'}
            className="w-full gap-2"
          >
            {entrandoComoConvidado ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            Try it without an account
          </Button>
          <p className="mt-2 text-center text-xs text-neutral-400">
            Opens a read-only sample library for 60 minutes. Nothing you type is kept after that.
          </p>
          <div className="mt-5 flex items-center gap-3 text-[10px] font-mono uppercase tracking-widest text-neutral-500">
            <span className="h-px flex-1 bg-white/10" />
            or use an account
            <span className="h-px flex-1 bg-white/10" />
          </div>
        </div>
      )}

      {/* tab switcher */}
      <div className="flex items-center gap-2 mb-6">
        <button
          type="button"
          onClick={() => { setMode('login'); setMsg(''); setStatus('idle') }}
          className={`px-3 py-1.5 rounded-full text-[10px] font-mono uppercase tracking-widest transition-colors ${
            mode === 'login'
              ? 'bg-blue-400/15 text-blue-200 ring-1 ring-blue-400/30'
              : 'text-neutral-400 hover:text-white'
          }`}
        >
          Sign in
        </button>
        <button
          type="button"
          onClick={() => { setMode('signup'); setMsg(''); setStatus('idle') }}
          className={`px-3 py-1.5 rounded-full text-[10px] font-mono uppercase tracking-widest transition-colors ${
            mode === 'signup'
              ? 'bg-blue-400/15 text-blue-200 ring-1 ring-blue-400/30'
              : 'text-neutral-400 hover:text-white'
          }`}
        >
          Create account
        </button>
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        {mode === 'signup' && (
          <div>
            <label htmlFor="auth-name" className="block text-[10px] font-mono uppercase tracking-widest text-neutral-400 mb-1.5">
              Full name
            </label>
            <Input
              id="auth-name"
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Your name"
              required
              autoComplete="name"
            />
          </div>
        )}

        <div>
          <label htmlFor="auth-email" className="block text-[10px] font-mono uppercase tracking-widest text-neutral-400 mb-1.5">
            Email
          </label>
          <Input
            id="auth-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
            autoComplete="username"
          />
        </div>

        <div>
          <label htmlFor="auth-password" className="block text-[10px] font-mono uppercase tracking-widest text-neutral-400 mb-1.5">
            Password
          </label>
          <Input
            id="auth-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === 'signup' ? 'min 12 characters' : '••••••••'}
            required
            minLength={mode === 'signup' ? 12 : 1}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          />
        </div>

        <Button
          type="submit"
          disabled={!email || !password || (mode === 'signup' && !fullName) || status === 'loading'}
          className="w-full mt-2 gap-2"
        >
          {status === 'loading' ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading…
            </>
          ) : mode === 'login' ? (
            <>Sign in <ArrowRight className="h-4 w-4" /></>
          ) : (
            <>Create account <ArrowRight className="h-4 w-4" /></>
          )}
        </Button>
      </form>

      {msg && status === 'error' && (
        <div className="mt-4 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 text-sm text-red-300">
          {msg}
        </div>
      )}

      <p className="mt-5 text-center text-xs text-neutral-400">
        {mode === 'login' ? (
          <>
            Don&apos;t have an account?{' '}
            <button
              type="button"
              onClick={() => { setMode('signup'); setMsg(''); setStatus('idle') }}
              className="font-medium text-blue-300 hover:text-blue-200 hover:underline"
            >
              Create one
            </button>
          </>
        ) : (
          <>
            Already have an account?{' '}
            <button
              type="button"
              onClick={() => { setMode('login'); setMsg(''); setStatus('idle') }}
              className="font-medium text-blue-300 hover:text-blue-200 hover:underline"
            >
              Sign in
            </button>
          </>
        )}
      </p>
    </div>
  )
}
