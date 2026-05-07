import Link from 'next/link'
import { Button } from '@/components/ui/button'
import RedirectIfAuthenticated from '@/components/RedirectIfAuthenticated'
import {
  FileStack, FileText, Cpu, Search, ShieldCheck, MessageSquare,
  Globe, ArrowRight, Users, Lock, FileImage, Mic, Video,
  Sparkles, CheckCircle2, Github,
} from 'lucide-react'

export const metadata = {
  title: 'BrainHub Team — Multi-modal team Q&A with cited answers',
  description:
    'Upload PDFs, images, audio, and video into a shared workspace. Ask anything in natural language, get grounded answers with citations from a Corrective RAG pipeline.',
}

const PIPELINE = [
  {
    icon: FileText,
    title: 'Multi-modal ingest',
    desc: 'PDFs go through pypdf. Images, audio, and video are extracted via Google Gemini. libmagic sniffs MIME at upload.',
    color: 'bg-blue-50 text-blue-600',
  },
  {
    icon: Cpu,
    title: 'Embed & index',
    desc: 'Voyage AI dual-tier embeddings (voyage-3-large for docs, voyage-3-lite for queries) + tsvector full-text search.',
    color: 'bg-purple-50 text-purple-600',
  },
  {
    icon: Search,
    title: 'Hybrid retrieval',
    desc: 'Semantic and keyword searches run in parallel, fused via Reciprocal Rank Fusion, then reranked by Cohere.',
    color: 'bg-emerald-50 text-emerald-600',
  },
  {
    icon: ShieldCheck,
    title: 'Corrective grading',
    desc: 'Retrieved chunks are scored. Below threshold? Query is rewritten and retried, or falls back to Tavily web search.',
    color: 'bg-amber-50 text-amber-600',
  },
  {
    icon: MessageSquare,
    title: 'Grounded synthesis',
    desc: 'Claude Sonnet writes the answer from verified context with strict source citations, streaming token-by-token.',
    color: 'bg-orange-50 text-orange-600',
  },
]

const MODALITIES = [
  { icon: FileText,  name: 'PDFs',   accent: 'from-blue-100 to-blue-50',     iconColor: 'text-blue-600' },
  { icon: FileImage, name: 'Images', accent: 'from-emerald-100 to-emerald-50', iconColor: 'text-emerald-600' },
  { icon: Mic,       name: 'Audio',  accent: 'from-violet-100 to-violet-50',  iconColor: 'text-violet-600' },
  { icon: Video,     name: 'Video',  accent: 'from-pink-100 to-pink-50',      iconColor: 'text-pink-600' },
]

const FEATURES = [
  { icon: Lock,        title: 'Tenant-isolated retrieval', desc: 'Every chunk row carries a user_id; vector and keyword queries WHERE-filter on it before RRF or rerank.' },
  { icon: Sparkles,    title: 'Multi-provider LLM',         desc: 'Anthropic native or any OpenRouter model via env var. Failover-ready.' },
  { icon: Globe,       title: 'Web fallback',                desc: 'When local context is too thin, the pipeline reaches Tavily for a web pass before answering.' },
  { icon: CheckCircle2,title: 'Strict citations',           desc: 'Every claim links back to a source chunk. No hallucinated references.' },
  { icon: Users,       title: 'Shared workspace',            desc: 'Everyone in the team sees the same documents; chat threads stay private per user.' },
  { icon: ShieldCheck, title: 'libmagic on upload',          desc: 'MIME is sniffed from bytes, not extension. Allowlist enforced server-side.' },
]

const STACK = [
  { label: 'LLM',         tech: 'Claude Sonnet + Haiku' },
  { label: 'Embeddings',  tech: 'Voyage AI (1536d)' },
  { label: 'Reranking',   tech: 'Cohere cross-encoder' },
  { label: 'Vector DB',   tech: 'PostgreSQL + pgvector HNSW' },
  { label: 'Cache',       tech: 'Redis 7' },
  { label: 'Backend',     tech: 'FastAPI, Python 3.11' },
  { label: 'Frontend',    tech: 'Next.js 14, React 18' },
  { label: 'Web fallback',tech: 'Tavily API' },
]

export default function Page() {
  return (
    <>
      <RedirectIfAuthenticated to="/chat" />
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
            <nav className="hidden sm:flex items-center gap-6 text-sm text-zinc-600">
              <a href="#modalities" className="hover:text-zinc-900 transition-colors">Modalities</a>
              <a href="#pipeline"   className="hover:text-zinc-900 transition-colors">How it works</a>
              <a href="#features"   className="hover:text-zinc-900 transition-colors">Features</a>
            </nav>
            <Link href="/login" className="text-sm font-medium text-white bg-zinc-900 hover:bg-zinc-800 transition-colors px-4 py-1.5 rounded-full">
              Sign in
            </Link>
          </div>
        </header>

        {/* Hero */}
        <section className="relative overflow-hidden">
          <div
            className="absolute inset-0 -z-0 pointer-events-none"
            aria-hidden
            style={{
              background:
                'radial-gradient(ellipse 70% 50% at 50% 0%, rgba(251,146,60,0.18), transparent 60%), radial-gradient(ellipse 50% 30% at 50% 100%, rgba(168,85,247,0.10), transparent 70%)',
            }}
          />
          <div className="relative max-w-6xl mx-auto px-6 sm:px-8 pt-16 pb-20 md:pt-24 md:pb-28 text-center">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass-panel border border-white/30 text-xs font-medium text-zinc-700 mb-6 shadow-sm">
              <Sparkles className="h-3.5 w-3.5 text-orange-500" />
              <span>Multi-modal RAG · Team-grade</span>
            </div>

            <h1 className="text-4xl sm:text-5xl md:text-6xl lg:text-7xl font-semibold tracking-tighter leading-[0.95] text-zinc-900 mb-6">
              Ask your team&apos;s
              <br />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-orange-500 via-amber-500 to-pink-500">
                PDFs, images, audio, video.
              </span>
            </h1>

            <p className="text-lg sm:text-xl text-zinc-700 leading-relaxed max-w-2xl mx-auto mb-10">
              One shared workspace. Four modalities. Cited answers.
              Powered by a Corrective RAG pipeline with hybrid search and cross-encoder reranking.
            </p>

            <div className="flex flex-wrap justify-center gap-3">
              <Link href="/login">
                <Button size="lg" className="rounded-full px-8 gap-2 btn-primary-gradient text-zinc-900 font-semibold shadow-orange-glow hover:opacity-90 transition-opacity">
                  Get Started <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
              <a href="#pipeline">
                <Button size="lg" variant="outline" className="rounded-full px-8 border-white/30 glass-panel text-zinc-700 hover:bg-white/40">
                  How It Works
                </Button>
              </a>
            </div>

            {/* Modality showcase */}
            <div id="modalities" className="mt-14 grid grid-cols-2 md:grid-cols-4 gap-3 max-w-3xl mx-auto">
              {MODALITIES.map((m) => (
                <div
                  key={m.name}
                  className={`relative aspect-[4/3] rounded-2xl border border-white/40 bg-gradient-to-br ${m.accent} shadow-genlabs flex flex-col items-center justify-center p-4 hover:-translate-y-0.5 transition-transform`}
                >
                  <m.icon className={`h-7 w-7 ${m.iconColor} mb-2`} strokeWidth={2} />
                  <span className="text-sm font-semibold text-zinc-800">{m.name}</span>
                  <span className="text-[10px] font-mono text-zinc-500 mt-0.5">supported</span>
                </div>
              ))}
            </div>

            <p className="mt-8 text-xs text-zinc-500 font-mono">
              libmagic MIME sniffing · 20 MB max upload · Gemini for non-PDF · Voyage AI embeddings
            </p>
          </div>
        </section>

        {/* Pipeline */}
        <section id="pipeline" className="py-16 sm:py-20 glass-panel border-t border-b border-white/20">
          <div className="max-w-6xl mx-auto px-6 sm:px-8">
            <div className="text-center mb-12">
              <span className="text-xs font-bold text-zinc-400 uppercase tracking-widest font-mono">Corrective RAG Pipeline</span>
              <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-zinc-900 mt-2">
                Five stages, end to end
              </h2>
              <p className="text-zinc-600 mt-3 max-w-2xl mx-auto">
                Every question goes through this exact path. If retrieval is weak, the pipeline corrects itself before answering.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              {PIPELINE.map((step, i) => (
                <div key={step.title} className="bg-white border border-zinc-200/80 rounded-2xl p-5 shadow-genlabs hover:shadow-lg transition-shadow">
                  <div className="flex items-center gap-2 mb-3">
                    <div className={`rounded-xl p-2 ${step.color}`}>
                      <step.icon className="h-4 w-4" />
                    </div>
                    <span className="text-[10px] font-mono text-zinc-400">{`0${i + 1}`}</span>
                  </div>
                  <h3 className="text-sm font-semibold text-zinc-900 mb-1.5">{step.title}</h3>
                  <p className="text-[11px] text-zinc-500 leading-relaxed">{step.desc}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Features */}
        <section id="features" className="py-16 sm:py-20">
          <div className="max-w-6xl mx-auto px-6 sm:px-8">
            <div className="text-center mb-12">
              <span className="text-xs uppercase tracking-widest text-zinc-400 font-mono">Why teams pick BrainHub</span>
              <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-zinc-900 mt-2">
                Engineered for production, not demos
              </h2>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              {FEATURES.map((f, i) => (
                <article key={i} className="rounded-2xl bg-white border border-zinc-200/80 p-6 shadow-genlabs hover:shadow-lg transition-shadow">
                  <div className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-zinc-100 text-zinc-700 mb-4">
                    <f.icon className="h-4 w-4" strokeWidth={2.4} />
                  </div>
                  <h3 className="font-semibold text-base text-zinc-900 mb-1.5">{f.title}</h3>
                  <p className="text-sm text-zinc-500 leading-relaxed">{f.desc}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* Tech stack */}
        <section className="py-16 sm:py-20 glass-panel border-t border-white/20">
          <div className="max-w-4xl mx-auto px-6 sm:px-8">
            <h2 className="text-2xl sm:text-3xl font-semibold tracking-tight text-zinc-900 mb-8 text-center">
              Built with
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {STACK.map((item) => (
                <div key={item.label} className="rounded-xl border border-zinc-200/80 bg-white p-4 shadow-genlabs text-center">
                  <p className="font-semibold text-zinc-900 text-xs uppercase tracking-wider">{item.label}</p>
                  <p className="text-[12px] text-zinc-500 mt-1">{item.tech}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Final CTA */}
        <section className="py-20">
          <div className="max-w-3xl mx-auto px-6 text-center">
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-zinc-900">
              Stop grepping your own Notion.
            </h2>
            <p className="text-zinc-600 mt-3 max-w-xl mx-auto">
              Drop everything your team has into one workspace. Ask anything. Get cited answers in seconds.
            </p>
            <Link href="/login">
              <Button size="lg" className="mt-8 rounded-full px-8 gap-2 btn-primary-gradient text-zinc-900 font-semibold shadow-orange-glow hover:opacity-90 transition-opacity">
                Create your workspace <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
          </div>
        </section>

        {/* Footer */}
        <footer className="py-8 border-t border-white/20 glass-panel">
          <div className="max-w-6xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-3 text-sm text-zinc-600">
            <p>Built by Pedro Gomes — full-stack AI engineer.</p>
            <div className="flex items-center gap-4">
              <a
                href="https://github.com/devpedrogomes/group-documents"
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-zinc-900 transition-colors inline-flex items-center gap-1.5"
              >
                <Github className="h-3.5 w-3.5" /> GitHub
              </a>
              <Link href="/login" className="hover:text-zinc-900 transition-colors">
                Sign in →
              </Link>
            </div>
          </div>
        </footer>
      </main>
    </>
  )
}
