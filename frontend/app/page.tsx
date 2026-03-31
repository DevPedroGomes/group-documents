'use client'

import { useEffect, useState } from 'react'
import KnowledgeHub from '@/components/KnowledgeHub'
import Topbar from '@/components/Topbar'
import { createClient } from '@/lib/supabase/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { FileStack, Loader2 } from 'lucide-react'

const supabase = createClient()

export default function Page() {
  const [session, setSession] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [authError, setAuthError] = useState<string | null>(null)

  useEffect(() => {
    // Check for error in URL params
    const params = new URLSearchParams(window.location.search)
    const error = params.get('error')
    if (error) {
      setAuthError(error)
      window.history.replaceState({}, '', '/')
    }

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })

    const { data: listener } = supabase.auth.onAuthStateChange((_e, s) => {
      setSession(s)
    })

    return () => listener.subscription.unsubscribe()
  }, [])

  if (loading) {
    return (
      <main className="min-h-dvh flex items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-4">
          <Skeleton className="h-12 w-12 rounded-xl" />
          <Skeleton className="h-4 w-32" />
        </div>
      </main>
    )
  }

  if (!session) {
    return (
      <main className="min-h-dvh flex items-center justify-center bg-muted/30 p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <FileStack className="h-6 w-6" />
            </div>
            <CardTitle className="text-2xl">Document Hub</CardTitle>
            <CardDescription>
              Sign in to manage your documents and chat with AI
            </CardDescription>
          </CardHeader>
          <CardContent>
            {authError && (
              <div className="mb-4 p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive text-sm">
                {authError}
              </div>
            )}
            <AuthForm />
          </CardContent>
        </Card>
      </main>
    )
  }

  return (
    <main className="h-dvh flex flex-col">
      <Topbar
        email={session.user.email}
        onSignOut={() => supabase.auth.signOut()}
      />
      <KnowledgeHub
        getToken={async () =>
          (await supabase.auth.getSession()).data.session?.access_token
        }
      />
    </main>
  )
}

function AuthForm() {
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [msg, setMsg] = useState('')

  const onEmailAuth = async (e: React.FormEvent) => {
    e.preventDefault()
    setStatus('loading')
    setMsg('')

    const { error } =
      mode === 'login'
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({
            email,
            password,
            options: {
              emailRedirectTo: `${window.location.origin}/auth/callback`,
            },
          })

    if (error) {
      setStatus('error')
      setMsg(error.message)
    } else if (mode === 'signup') {
      setStatus('idle')
      setMsg('Account created! Check your email to confirm, or login now.')
    }
  }

  const onGoogleAuth = async () => {
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    })
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onEmailAuth} className="space-y-3">
        <div>
          <label className="block text-sm font-medium mb-1.5">Email</label>
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
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
          />
        </div>

        <Button
          type="submit"
          disabled={!email || !password || status === 'loading'}
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
              : 'bg-green-50 text-green-700'
          }`}
        >
          {msg}
        </div>
      )}

      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-card px-2 text-muted-foreground">or</span>
        </div>
      </div>

      <Button
        variant="outline"
        onClick={onGoogleAuth}
        type="button"
        className="w-full"
      >
        <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24">
          <path
            fill="#4285F4"
            d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
          />
          <path
            fill="#34A853"
            d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
          />
          <path
            fill="#FBBC05"
            d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
          />
          <path
            fill="#EA4335"
            d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
          />
        </svg>
        Continue with Google
      </Button>

      <p className="text-center text-sm text-muted-foreground">
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
