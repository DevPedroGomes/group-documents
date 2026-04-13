'use client'

import { useState } from 'react'
import KnowledgeHub from '@/components/KnowledgeHub'
import Topbar from '@/components/Topbar'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  FileStack, Loader2, FileText, Cpu, Search, ShieldCheck, MessageSquare,
  Globe, ArrowRight, Users, Lock
} from 'lucide-react'

// ─── Pipeline Steps ───

const pipelineSteps = [
  {
    icon: FileText,
    title: 'Document Ingestion',
    desc: 'Upload PDFs, images, audio, or video (up to 20MB). Content is extracted, split into semantic chunks (500 tokens, 100 overlap), and enriched with contextual summaries via Claude Haiku.',
    color: 'bg-blue-50 text-blue-600',
  },
  {
    icon: Cpu,
    title: 'Embedding & Indexing',
    desc: 'Each chunk is embedded with Voyage AI (voyage-4-large, 1536 dims) and stored in PostgreSQL + pgvector with HNSW indexing. Full-text search vectors (tsvector + GIN) are generated in parallel.',
    color: 'bg-purple-50 text-purple-600',
  },
  {
    icon: Search,
    title: 'Hybrid Search + Reranking',
    desc: 'Queries trigger multi-query expansion, then semantic (cosine similarity) and keyword (tsvector) searches run in parallel. Results are fused via Reciprocal Rank Fusion (k=60) and reranked with Cohere cross-encoder.',
    color: 'bg-emerald-50 text-emerald-600',
  },
  {
    icon: ShieldCheck,
    title: 'Corrective RAG',
    desc: 'Retrieved chunks are graded for relevance. If below threshold (0.7), the query is automatically transformed and retried, or falls back to Tavily web search. Ensures answers are always grounded.',
    color: 'bg-amber-50 text-amber-600',
  },
  {
    icon: MessageSquare,
    title: 'Grounded Generation',
    desc: 'Claude Sonnet synthesizes the answer from verified context with strict source attribution. Responses stream token-by-token via SSE with citation markers linking back to source documents.',
    color: 'bg-orange-50 text-orange-600',
  },
]

const techStack = [
  { label: 'LLM', tech: 'Anthropic Claude Sonnet + Haiku' },
  { label: 'Embeddings', tech: 'Voyage AI (1536d)' },
  { label: 'Reranking', tech: 'Cohere cross-encoder' },
  { label: 'Vector DB', tech: 'PostgreSQL + pgvector (HNSW)' },
  { label: 'Backend', tech: 'FastAPI, Python 3.11' },
  { label: 'Frontend', tech: 'Next.js 14, React 18' },
  { label: 'Workflow', tech: 'LangGraph state machine' },
  { label: 'Web Fallback', tech: 'Tavily API' },
]

// ─── Main Page ───

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

  // ─── Authenticated: show app ───
  if (user) {
    return (
      <main className="h-dvh flex flex-col bg-zinc-400/80">
        <div className="flex flex-col h-full xl:max-w-[1400px] xl:mx-auto xl:my-4 glass-panel xl:rounded-[2rem] xl:border xl:border-white/20 xl:shadow-2xl overflow-hidden">
          <Topbar email={user.email || ''} onSignOut={logout} />
          <KnowledgeHub getToken={async () => getToken() || undefined} />
        </div>
      </main>
    )
  }

  // ─── Public: landing page ───
  return (
    <main className="min-h-dvh bg-zinc-400/80">
      {/* Navbar */}
      <header className="sticky top-0 z-50 w-full border-b border-white/20 glass-panel">
        <div className="max-w-6xl mx-auto flex h-14 items-center justify-between px-6 sm:px-8">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-full btn-primary-gradient shadow-orange-glow">
              <FileStack className="h-4 w-4" />
            </div>
            <span className="font-semibold tracking-tight text-zinc-900">BrainHub Team</span>
          </div>
          <a href="#auth" className="text-sm font-medium text-zinc-700 hover:text-zinc-900 transition-colors">
            Sign In
          </a>
        </div>
      </header>

      {/* Hero */}
      <section className="py-20 md:py-28">
        <div className="max-w-6xl mx-auto px-6 sm:px-8 text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass-panel border border-white/30 text-xs font-medium text-zinc-600 mb-6">
            <Users className="h-3.5 w-3.5" />
            Collaborative Document Q&A for Teams
          </div>

          <h1 className="text-4xl sm:text-5xl md:text-6xl font-semibold tracking-tighter leading-[0.95] text-zinc-900 mb-6">
            Ask your documents.
            <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-orange-500 to-amber-500">
              Get grounded answers.
            </span>
          </h1>

          <p className="text-lg text-zinc-600 leading-relaxed max-w-2xl mx-auto mb-10">
            Upload PDFs, images, audio, and video into a shared team workspace.
            Ask questions in natural language and get AI-powered answers with
            automatic source citations, powered by a Corrective RAG pipeline
            with hybrid search and cross-encoder reranking.
          </p>

          <div className="flex flex-wrap justify-center gap-3">
            <a href="#auth">
              <Button size="lg" className="rounded-full px-8 gap-2 btn-primary-gradient text-zinc-900 font-semibold shadow-orange-glow hover:opacity-90 transition-opacity">
                Get Started <ArrowRight className="h-4 w-4" />
              </Button>
            </a>
            <a href="#pipeline">
              <Button size="lg" variant="outline" className="rounded-full px-8 border-white/30 glass-panel text-zinc-700 hover:bg-white/40">
                How It Works
              </Button>
            </a>
          </div>

          <div className="flex flex-wrap justify-center gap-x-6 gap-y-1 mt-8 text-xs text-zinc-500 font-mono">
            <span className="flex items-center gap-1.5"><Users className="h-3 w-3" /> Shared documents</span>
            <span className="flex items-center gap-1.5"><Lock className="h-3 w-3" /> Private chat threads</span>
            <span className="flex items-center gap-1.5"><Globe className="h-3 w-3" /> Web search fallback</span>
          </div>
        </div>
      </section>

      {/* Pipeline */}
      <section id="pipeline" className="py-16 glass-panel border-t border-b border-white/20">
        <div className="max-w-6xl mx-auto px-6 sm:px-8">
          <div className="flex items-center gap-4 mb-3">
            <span className="text-xs font-bold text-zinc-400 uppercase tracking-widest font-mono">Corrective RAG Pipeline</span>
            <div className="h-px flex-1 bg-zinc-300/50" />
          </div>
          <h2 className="text-2xl font-semibold tracking-tight text-zinc-900 mb-2">
            How It Works
          </h2>
          <p className="text-zinc-600 mb-8 max-w-2xl">
            Every question goes through a 5-stage pipeline that retrieves, validates,
            and synthesizes answers exclusively from your team&apos;s documents.
          </p>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {pipelineSteps.map((step, i) => (
              <div key={step.title} className="bg-white border border-zinc-200/80 rounded-2xl p-4 shadow-genlabs hover:shadow-lg transition-shadow">
                <div className="flex items-center gap-2 mb-3">
                  <div className={`rounded-xl p-2 ${step.color}`}>
                    <step.icon className="h-4 w-4" />
                  </div>
                  <span className="text-[10px] font-mono text-zinc-400">{i + 1}</span>
                </div>
                <h3 className="text-xs font-semibold text-zinc-900 mb-1">{step.title}</h3>
                <p className="text-[11px] text-zinc-500 leading-relaxed">{step.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Tech Stack */}
      <section className="py-16">
        <div className="max-w-6xl mx-auto px-6 sm:px-8">
          <h2 className="text-2xl font-semibold tracking-tight text-zinc-900 mb-8 text-center">
            Tech Stack
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 max-w-3xl mx-auto">
            {techStack.map((item) => (
              <div key={item.label} className="rounded-xl border border-zinc-200/80 bg-white p-3 shadow-genlabs text-center">
                <p className="font-semibold text-zinc-900 text-xs">{item.label}</p>
                <p className="text-[11px] text-zinc-500 mt-0.5">{item.tech}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Auth Section */}
      <section id="auth" className="py-20 glass-panel border-t border-white/20">
        <div className="max-w-md mx-auto px-6 sm:px-8">
          <Card className="glass-panel border-white/20">
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
        </div>
      </section>

      {/* Footer */}
      <footer className="py-8 text-center">
        <p className="text-sm text-zinc-500">
          Built with FastAPI, Next.js, LangGraph, and Anthropic Claude
        </p>
      </footer>
    </main>
  )
}

// ─── Auth Form (unchanged logic) ───

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
              onClick={() => { setMode('signup'); setMsg('') }}
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
              onClick={() => { setMode('login'); setMsg('') }}
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
