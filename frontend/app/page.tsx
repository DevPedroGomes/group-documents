'use client'

import { useState } from 'react'
import KnowledgeHub from '@/components/KnowledgeHub'
import Topbar from '@/components/Topbar'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { FileStack, Loader2 } from 'lucide-react'

export default function Page() {
  const { user, loading, logout, getToken } = useAuth()

  if (loading) {
    return (
      <main className="min-h-dvh flex items-center justify-center bg-zinc-400/80">
        <div className="flex flex-col items-center gap-4">
          <Skeleton className="h-12 w-12 rounded-full" />
          <Skeleton className="h-4 w-32 rounded-full" />
        </div>
      </main>
    )
  }

  if (!user) {
    return (
      <main className="min-h-dvh flex items-center justify-center bg-zinc-400/80 p-4">
        <Card className="w-full max-w-md glass-panel border-white/20">
          <CardHeader className="text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full btn-primary-gradient shadow-orange-glow">
              <FileStack className="h-6 w-6" />
            </div>
            <CardTitle className="text-2xl tracking-tighter">BrainHub Team</CardTitle>
            <CardDescription>
              Sign in to manage your documents and chat with AI
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AuthForm />
          </CardContent>
        </Card>
      </main>
    )
  }

  const email = user.email || ''

  return (
    <main className="h-dvh flex flex-col bg-zinc-400/80">
      <div className="flex flex-col h-full xl:max-w-[1400px] xl:mx-auto xl:my-4 glass-panel xl:rounded-[2rem] xl:border xl:border-white/20 xl:shadow-2xl overflow-hidden">
        <Topbar
          email={email}
          onSignOut={logout}
        />
        <KnowledgeHub
          getToken={async () => getToken() || undefined}
        />
      </div>
    </main>
  )
}

function AuthForm() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [msg, setMsg] = useState('')

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
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="space-y-3">
        {mode === 'signup' && (
          <div>
            <label className="block text-sm font-medium mb-1.5">Full Name</label>
            <Input
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
          <label className="block text-sm font-medium mb-1.5">Email</label>
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
            autoComplete="username"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1.5">Password</label>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
            minLength={6}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          />
        </div>

        <Button
          type="submit"
          disabled={!email || !password || (mode === 'signup' && !fullName) || status === 'loading'}
          className="w-full"
        >
          {status === 'loading' ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading...
            </>
          ) : mode === 'login' ? (
            'Sign In'
          ) : (
            'Create Account'
          )}
        </Button>
      </form>

      {msg && (
        <div
          className={`text-sm p-3 rounded-lg ${
            status === 'error'
              ? 'bg-destructive/10 text-destructive'
              : 'bg-emerald-50 text-emerald-700 border border-emerald-200'
          }`}
        >
          {msg}
        </div>
      )}

      <p className="text-center text-sm text-zinc-500">
        {mode === 'login' ? (
          <>
            Don&apos;t have an account?{' '}
            <button
              type="button"
              onClick={() => {
                setMode('signup')
                setMsg('')
              }}
              className="font-medium text-foreground hover:underline"
            >
              Sign up
            </button>
          </>
        ) : (
          <>
            Already have an account?{' '}
            <button
              type="button"
              onClick={() => {
                setMode('login')
                setMsg('')
              }}
              className="font-medium text-foreground hover:underline"
            >
              Sign in
            </button>
          </>
        )}
      </p>
    </div>
  )
}
