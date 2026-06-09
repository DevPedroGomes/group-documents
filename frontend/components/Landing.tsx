'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/contexts/AuthContext'
import Topbar from '@/components/Topbar'
import KnowledgeHub from '@/components/KnowledgeHub'
import { useLocale } from '@/hooks/useLocale'
import {
  FileStack, FileText, Cpu, Search, ShieldCheck, MessageSquare,
  Globe, ArrowRight, Users, Lock, FileImage, Mic, Video,
  Sparkles, CheckCircle2, Github, Bot, User, ArrowDown, Loader2,
} from 'lucide-react'

const SECTION_IDS = ['index', 'pipeline', 'features', 'stack'] as const

const PIPELINE = [
  {
    icon: FileText,
    title: 'Multi-modal ingest',
    desc: 'PDFs go through pypdf. Images, audio, and video are extracted via Google Gemini. libmagic sniffs MIME at upload, not extension.',
    trace: 'mime: application/pdf · 4.2MB · 287 chunks',
  },
  {
    icon: Cpu,
    title: 'Embed & index',
    desc: 'Voyage AI dual-tier embeddings — voyage-3-large for documents, voyage-3-lite for queries. tsvector built in parallel for keyword search.',
    trace: 'voyage-3-large · 1536d · pgvector HNSW',
  },
  {
    icon: Search,
    title: 'Hybrid retrieval',
    desc: 'Semantic and BM25 searches run in parallel, fused via Reciprocal Rank Fusion, then reranked by a Cohere cross-encoder for the final top-k.',
    trace: 'rrf_k=60 · top_k=12 · rerank=8',
  },
  {
    icon: ShieldCheck,
    title: 'Corrective grading',
    desc: 'Retrieved chunks are scored by an LLM grader. Below threshold? Query is rewritten and retried. Still weak? Falls back to Tavily web search.',
    trace: 'sim_threshold=0.72 · max_retries=2',
  },
  {
    icon: MessageSquare,
    title: 'Grounded synthesis',
    desc: 'Claude Sonnet writes the answer from verified context only, with strict source citations, streaming token-by-token.',
    trace: 'claude-sonnet-4 · stream=true · cite=strict',
  },
]

const MODALITIES = [
  { icon: FileText,  name: 'PDFs' },
  { icon: FileImage, name: 'Images' },
  { icon: Mic,       name: 'Audio' },
  { icon: Video,     name: 'Video' },
]

const STATS = [
  { value: '5',     label: 'Pipeline stages' },
  { value: '4',     label: 'Modalities' },
  { value: '1536',  label: 'Vector dimensions' },
  { value: '<2s',   label: 'P95 latency' },
]

const FEATURES = [
  {
    icon: Lock,
    label: '01 / Isolation',
    title: 'Tenant-isolated retrieval',
    desc: 'Every chunk row carries a user_id; vector and keyword queries WHERE-filter on it before RRF or rerank. No cross-tenant leakage path exists at the SQL layer.',
  },
  {
    icon: Sparkles,
    label: '02 / LLM',
    title: 'Multi-provider routing',
    desc: 'Anthropic native or any OpenRouter model via env var. The synthesis call is the only one that swaps; embeddings and reranking stay on Voyage and Cohere.',
  },
  {
    icon: Globe,
    label: '03 / Fallback',
    title: 'Tavily web pass on weak context',
    desc: 'When local retrieval scores below threshold and rewriting fails, the pipeline performs a Tavily web pass before answering — never silently fabricates.',
  },
  {
    icon: CheckCircle2,
    label: '04 / Citations',
    title: 'Strict source binding',
    desc: 'Every claim links back to a specific chunk by id. The synthesis prompt refuses to answer if the cited spans cannot be attributed.',
  },
  {
    icon: Users,
    label: '05 / Workspace',
    title: 'Shared corpus, private threads',
    desc: 'Everyone in the team sees the same documents and benefits from the same retrieval. Chat threads and prompt history stay private per user.',
  },
  {
    icon: ShieldCheck,
    label: '06 / Upload',
    title: 'libmagic byte sniffing',
    desc: 'MIME is read from bytes, not file extension. Allowlist enforced server-side; renamed payloads are rejected before they touch storage.',
  },
]

const STACK = [
  'Claude Sonnet',
  'Voyage AI 3-large',
  'Cohere Rerank',
  'pgvector HNSW',
  'PostgreSQL 16',
  'Redis 7',
  'FastAPI',
  'Next.js 16',
  'React 19',
  'Turbopack',
  'Tavily',
  'Google Gemini',
  'libmagic',
]

export default function Landing() {
  const { user, loading, logout, getToken } = useAuth()
  const { locale, toggleLocale, t } = useLocale()

  // ─── Loading auth state ──────────────────────────────────────────────
  if (loading) {
    return (
      <main className="min-h-dvh flex items-center justify-center px-6">
        <div className="flex flex-col items-center gap-6 fade-slide-in">
          <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
            00 / Booting
          </span>

          {/* Brand mark with pulsing ring */}
          <div className="flex items-center gap-3">
            <div
              className="relative flex h-10 w-10 items-center justify-center rounded-full bg-white/10 ring-1 ring-white/15 backdrop-blur"
              style={{ borderRadius: 9999 }}
            >
              <FileStack className="h-4 w-4 text-white" />
              <span
                className="absolute inset-0 rounded-full ring-2 ring-blue-400/40 animate-ping"
                style={{ borderRadius: 9999 }}
                aria-hidden
              />
            </div>
            <span className="font-semibold tracking-tight text-white text-lg">BrainHub</span>
          </div>

          {/* Terminal-style status line */}
          <div className="flex items-center gap-3 text-[11px] font-mono text-neutral-500">
            <span className="text-blue-300">$</span>
            <span>checking session</span>
            <span
              className="ml-1 inline-block w-1.5 h-3 bg-blue-300/80 animate-pulse"
              aria-hidden
            />
          </div>
        </div>
      </main>
    )
  }

  // ─── Authenticated: render the app shell (Topbar + Library) ──────────
  if (user) {
    return (
      <main className="h-dvh flex flex-col">
        <div className="flex flex-col h-full xl:max-w-[1400px] xl:mx-auto xl:my-4 glass-panel xl:rounded-[2rem] xl:ring-1 xl:ring-white/10 xl:shadow-2xl xl:shadow-black/40 overflow-hidden">
          <Topbar email={user.email || ''} onSignOut={logout} />
          <KnowledgeHub getToken={async () => getToken() || undefined} />
        </div>
      </main>
    )
  }

  // ─── Public: marketing landing ───────────────────────────────────────
  return (
    <main className="relative text-white">
      {/* ─── Top bar ─────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 w-full border-b border-white/10 glass-panel">
        <div className="max-w-7xl mx-auto flex h-14 items-center justify-between px-6 sm:px-8">
          <div className="flex items-center gap-2.5">
            <div
              className="flex h-8 w-8 items-center justify-center rounded-full bg-white/10 backdrop-blur border-gradient"
              style={{ borderRadius: 9999 }}
            >
              <FileStack className="h-4 w-4 text-white" />
            </div>
            <span className="font-semibold tracking-tight text-white">BrainHub</span>
            <span className="hidden sm:inline-block text-[11px] font-mono text-neutral-500 ml-1">/ team</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={toggleLocale}
              className="flex items-center gap-1 rounded-md border border-white/10 px-2.5 py-1 text-xs font-medium text-neutral-300 transition-colors hover:bg-white/5"
              aria-label="Toggle language"
            >
              <span className={locale === 'en' ? 'font-bold text-white' : ''}>EN</span>
              <span className="text-neutral-600">|</span>
              <span className={locale === 'pt' ? 'font-bold text-white' : ''}>PT</span>
            </button>
            <a
              href="#auth"
              className="text-sm font-medium text-neutral-900 bg-white hover:bg-neutral-100 transition-colors px-4 py-1.5 rounded-full"
            >
              {t('nav.signIn')}
            </a>
          </div>
        </div>
      </header>

      {/* ─── Sticky section index (lg+) ─────────────────────────────── */}
      <aside
        className="hidden lg:flex flex-col gap-3 fixed left-6 top-1/2 -translate-y-1/2 z-30"
        aria-label="Section index"
      >
        {SECTION_IDS.map((id, i) => (
          <a
            key={id}
            href={`#${id}`}
            className="group flex items-center gap-3 text-[10px] font-mono uppercase tracking-widest text-neutral-500 hover:text-white transition-colors"
          >
            <span className="w-6 text-right">{`0${i + 1}`}</span>
            <span className="h-px w-6 bg-white/10 group-hover:w-10 group-hover:bg-blue-300 transition-all" />
            <span className="opacity-0 group-hover:opacity-100 transition-opacity">{t(`sections.${id}` as const)}</span>
          </a>
        ))}
      </aside>

      {/* ─── 01 · Asymmetric hero with chat preview ─────────────────── */}
      <section
        id="index"
        className="max-w-7xl mx-auto px-6 sm:px-8 pt-16 pb-20 md:pt-20 md:pb-24"
      >
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 lg:gap-12 items-center">
          {/* Left column — text */}
          <div className="lg:col-span-7">
            <div className="flex items-center gap-3 mb-8">
              <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                {t('hero.tag')}
              </span>
              <span className="h-px flex-1 max-w-[80px] bg-white/10" />
              <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500">
                {t('hero.version')}
              </span>
            </div>

            <h1 className="text-5xl sm:text-6xl md:text-7xl font-semibold tracking-tighter leading-[0.95] text-white mb-7">
              {t('hero.title1')}
              <br />
              {t('hero.title2')}
              <br />
              <span className="gradient-text">{t('hero.title3')}</span>
            </h1>

            <p className="text-base sm:text-lg text-neutral-400 leading-relaxed max-w-xl mb-7">
              {t('hero.subtitle')}
            </p>

            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2 mb-8">
              {MODALITIES.map((m, i) => (
                <span key={m.name} className="inline-flex items-center gap-1.5 text-xs text-neutral-300">
                  <m.icon className="h-3.5 w-3.5 text-blue-300" strokeWidth={1.6} />
                  <span>{m.name}</span>
                  {i < MODALITIES.length - 1 && (
                    <span className="text-neutral-700 ml-1">·</span>
                  )}
                </span>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <a href="#auth">
                <Button size="lg" className="rounded-full px-7 gap-2">
                  {t('hero.cta.open')} <ArrowRight className="h-4 w-4" />
                </Button>
              </a>
              <a href="#pipeline">
                <Button size="lg" variant="outline" className="rounded-full px-7 gap-2">
                  {t('hero.cta.read')} <ArrowDown className="h-4 w-4" />
                </Button>
              </a>
            </div>
          </div>

          {/* Right column — chat preview mock */}
          <div className="lg:col-span-5 lg:pl-4">
            <div
              className="relative rounded-3xl bg-white/[0.04] ring-1 ring-white/10 border-gradient backdrop-blur p-5 sm:p-6 fade-slide-in"
              style={{ borderRadius: 24 }}
            >
              <div className="flex items-center justify-between text-[10px] font-mono text-neutral-500 mb-5">
                <div className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-blue-400/70" />
                  <span className="uppercase tracking-widest">live trace</span>
                </div>
                <span>thread / 8a3f</span>
              </div>

              <div className="flex justify-end mb-4">
                <div className="max-w-[85%] flex items-start gap-2.5 flex-row-reverse">
                  <div className="h-7 w-7 rounded-full bg-white shrink-0 flex items-center justify-center">
                    <User className="h-3.5 w-3.5 text-neutral-900" />
                  </div>
                  <div className="rounded-2xl rounded-tr-sm bg-white text-neutral-900 px-3.5 py-2 text-sm">
                    What did finance flag in last quarter&apos;s vendor audit?
                  </div>
                </div>
              </div>

              <div className="flex items-start gap-2.5 mb-3">
                <div className="h-7 w-7 rounded-full bg-white/5 ring-1 ring-white/10 shrink-0 flex items-center justify-center">
                  <Bot className="h-3.5 w-3.5 text-blue-300" />
                </div>
                <div className="flex-1 max-w-[85%]">
                  <div className="rounded-2xl rounded-tl-sm bg-white/5 ring-1 ring-white/10 px-3.5 py-2.5 text-sm text-neutral-100 leading-relaxed">
                    Two issues surfaced. First, three vendors lacked SOC 2 attestation by Q3 close.
                    Second, an unbudgeted <span className="text-blue-300">$48k overrun</span> on the analytics line —
                    flagged as material in the audit memo.
                  </div>

                  <div className="flex flex-wrap gap-1.5 mt-2.5">
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-400/15 border border-blue-400/30 text-[10px] text-blue-200">
                      <FileText className="h-3 w-3" />
                      <span className="max-w-[120px] truncate">Vendor-Audit-Q3.pdf</span>
                      <span className="text-blue-300/80 font-mono">p.4</span>
                    </span>
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-400/15 border border-blue-400/30 text-[10px] text-blue-200">
                      <FileText className="h-3 w-3" />
                      <span className="max-w-[120px] truncate">Finance-Memo-Oct.pdf</span>
                      <span className="text-blue-300/80 font-mono">p.2</span>
                    </span>
                  </div>
                </div>
              </div>

              <div className="mt-5 pt-3 border-t border-white/5 flex items-center justify-between text-[10px] font-mono text-neutral-500">
                <span>retrieval: 12 → rerank: 8 → cite: 2</span>
                <span className="text-emerald-300">1.4s</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ─── 02 · Stat band ──────────────────────────────────────────── */}
      <section className="border-y border-white/10 glass-panel">
        <div className="max-w-7xl mx-auto px-6 sm:px-8 py-10 sm:py-12">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6 md:gap-0 md:divide-x md:divide-white/10">
            {STATS.map((s, i) => (
              <div key={s.label} className={i === 0 ? '' : 'md:pl-10'}>
                <p className="text-4xl sm:text-5xl font-semibold tracking-tighter text-white">
                  {s.value}
                </p>
                <p className="mt-2 text-[10px] font-mono uppercase tracking-widest text-neutral-500">
                  {s.label}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── 03 · Pipeline as vertical timeline ──────────────────────── */}
      <section
        id="pipeline"
        className="max-w-7xl mx-auto px-6 sm:px-8 py-20 sm:py-24"
      >
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 mb-12">
          <div className="lg:col-span-4">
            <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
              02 / Pipeline
            </span>
            <h2 className="mt-3 text-3xl sm:text-4xl font-semibold tracking-tighter text-white">
              Five stages.
              <br />
              <span className="text-neutral-500">No shortcuts.</span>
            </h2>
          </div>
          <div className="lg:col-span-8 lg:pt-10">
            <p className="text-neutral-400 text-base leading-relaxed max-w-2xl">
              Every question follows the exact same path. When retrieval is weak, the pipeline
              rewrites the query or falls back to web — it never silently lowers the bar to keep
              up appearances.
            </p>
          </div>
        </div>

        <ol className="relative">
          <span
            className="absolute left-[1.5rem] sm:left-[3.25rem] top-0 bottom-0 w-px bg-white/10"
            aria-hidden
          />

          {PIPELINE.map((step, i) => (
            <li
              key={step.title}
              className="relative grid grid-cols-1 sm:grid-cols-12 gap-4 sm:gap-8 py-7 hairline-top first:border-t-0"
            >
              <div className="sm:col-span-3 flex items-center gap-4">
                <span className="relative z-10 flex h-12 w-12 sm:h-[3.25rem] sm:w-[3.25rem] items-center justify-center rounded-full bg-neutral-950 ring-1 ring-white/15 text-blue-300">
                  <step.icon className="h-4 w-4" strokeWidth={1.8} />
                </span>
                <span className="text-3xl sm:text-4xl font-semibold tracking-tighter text-white sm:hidden">
                  {`0${i + 1}`}
                </span>
                <span className="hidden sm:block text-3xl font-semibold tracking-tighter text-white/80">
                  {`0${i + 1}`}
                </span>
              </div>

              <div className="sm:col-span-6">
                <h3 className="text-lg font-semibold text-white tracking-tight">{step.title}</h3>
                <p className="mt-1.5 text-sm text-neutral-400 leading-relaxed">{step.desc}</p>
              </div>

              <div className="sm:col-span-3 flex sm:justify-end items-start sm:pt-1">
                <span className="trace-chip">{step.trace}</span>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* ─── 04 · Editorial features (zigzag, no cards) ──────────────── */}
      <section
        id="features"
        className="border-t border-white/10"
      >
        <div className="max-w-7xl mx-auto px-6 sm:px-8 py-20 sm:py-24">
          <div className="flex items-end justify-between mb-12 gap-6">
            <div>
              <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                03 / Engineering
              </span>
              <h2 className="mt-3 text-3xl sm:text-4xl font-semibold tracking-tighter text-white">
                Built where the demos stop.
              </h2>
            </div>
            <p className="hidden md:block text-xs text-neutral-500 font-mono uppercase tracking-widest">
              six choices that matter
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2">
            {FEATURES.map((f, i) => {
              const isLeft = i % 2 === 0
              return (
                <article
                  key={f.title}
                  className={`relative py-8 sm:py-10 px-6 sm:px-8 ${
                    isLeft ? 'md:pr-12' : 'md:pl-12 md:border-l md:border-white/10'
                  } ${i >= 2 ? 'border-t border-white/10' : 'md:border-t-0 border-t border-white/10 first:border-t-0'} ${
                    i === 1 ? 'md:border-t-0' : ''
                  }`}
                >
                  <div className="flex items-center gap-3 mb-4">
                    <span className="inline-flex items-center justify-center h-8 w-8 rounded-lg bg-white/5 ring-1 ring-white/10 text-blue-300">
                      <f.icon className="h-4 w-4" strokeWidth={1.8} />
                    </span>
                    <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500">
                      {f.label}
                    </span>
                  </div>
                  <h3 className="text-xl font-semibold text-white tracking-tight mb-2">{f.title}</h3>
                  <p className="text-sm text-neutral-400 leading-relaxed max-w-md">{f.desc}</p>
                </article>
              )
            })}
          </div>
        </div>
      </section>

      {/* ─── 05 · Stack marquee ──────────────────────────────────────── */}
      <section
        id="stack"
        className="border-t border-white/10 glass-panel py-12 sm:py-14 overflow-hidden"
      >
        <div className="max-w-7xl mx-auto px-6 sm:px-8 mb-6">
          <div className="flex items-baseline gap-3">
            <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
              04 / Stack
            </span>
            <span className="h-px flex-1 bg-white/10" />
            <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500">
              no fluff
            </span>
          </div>
        </div>

        <div className="marquee-mask">
          <div className="marquee-track">
            {[...STACK, ...STACK].map((tech, i) => (
              <span
                key={`${tech}-${i}`}
                className="flex items-center gap-6 px-6 text-2xl sm:text-3xl font-semibold tracking-tighter text-white/70 whitespace-nowrap"
              >
                {tech}
                <span className="text-blue-400/40 text-base">●</span>
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ─── 06 · Auth (CTA + form, asymmetric split) ────────────────── */}
      <section id="auth" className="border-t border-white/10">
        <div className="max-w-6xl mx-auto px-6 sm:px-8 py-20 sm:py-24">
          <div className="grid grid-cols-1 md:grid-cols-12 gap-10 lg:gap-14 items-start">
            {/* Left — editorial CTA copy */}
            <div className="md:col-span-7 md:pt-6">
              <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                Get started
              </span>
              <h2 className="mt-3 text-4xl sm:text-5xl md:text-6xl font-semibold tracking-tighter leading-[1.0] text-white">
                Stop grepping
                <br />
                your own Notion.
              </h2>
              <p className="mt-5 text-neutral-400 text-base max-w-lg leading-relaxed">
                Drop everything your team has into one workspace. Ask anything.
                Get cited answers in seconds.
              </p>

              {/* terminal flourish */}
              <div className="mt-12 flex items-center gap-3 text-[11px] font-mono text-neutral-500">
                <span className="text-blue-300">$</span>
                <span>brainhub</span>
                <span className="text-neutral-700">init</span>
                <span className="text-neutral-700">--workspace</span>
                <span className="text-blue-300">your-team</span>
                <span className="ml-1 inline-block w-2 h-3.5 bg-blue-300/80 animate-pulse" />
              </div>
            </div>

            {/* Right — auth form */}
            <div className="md:col-span-5">
              <AuthForm />
            </div>
          </div>
        </div>
      </section>

      {/* ─── Footer ─────────────────────────────────────────────────── */}
      <footer className="border-t border-white/10 glass-panel">
        <div className="max-w-7xl mx-auto px-6 sm:px-8 py-8 flex flex-col sm:flex-row items-center justify-between gap-3 text-sm text-neutral-400">
          <p>
            &copy; {new Date().getFullYear()} · Built by{' '}
            <a href="https://pgdev.com.br" className="font-medium text-neutral-200 hover:text-white transition-colors">
              Pedro Gomes
            </a>
          </p>
          <div className="flex items-center gap-5">
            <a
              href="https://github.com/devpedrogomes/group-documents"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors inline-flex items-center gap-1.5"
            >
              <Github className="h-3.5 w-3.5" /> GitHub
            </a>
            <a
              href="https://www.linkedin.com/in/devpgomes"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors"
            >
              LinkedIn
            </a>
            <a
              href="https://pgdev.com.br"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors"
            >
              Portfolio
            </a>
            <a href="#auth" className="hover:text-white transition-colors">
              Sign in →
            </a>
          </div>
        </div>
      </footer>
    </main>
  )
}

// ─── Auth Form (login / signup tabs) ─────────────────────────────────
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
    <div
      className="relative rounded-3xl bg-white/[0.04] ring-1 ring-white/10 border-gradient backdrop-blur p-6 sm:p-7"
      style={{ borderRadius: 24 }}
    >
      {/* tab switcher */}
      <div className="flex items-center gap-2 mb-6">
        <button
          type="button"
          onClick={() => { setMode('login'); setMsg(''); setStatus('idle') }}
          className={`px-3 py-1.5 rounded-full text-[10px] font-mono uppercase tracking-widest transition-colors ${
            mode === 'login'
              ? 'bg-blue-400/15 text-blue-200 ring-1 ring-blue-400/30'
              : 'text-neutral-500 hover:text-white'
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
              : 'text-neutral-500 hover:text-white'
          }`}
        >
          Create account
        </button>
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        {mode === 'signup' && (
          <div>
            <label className="block text-[10px] font-mono uppercase tracking-widest text-neutral-500 mb-1.5">
              Full name
            </label>
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
          <label className="block text-[10px] font-mono uppercase tracking-widest text-neutral-500 mb-1.5">
            Email
          </label>
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
          <label className="block text-[10px] font-mono uppercase tracking-widest text-neutral-500 mb-1.5">
            Password
          </label>
          <Input
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

      <p className="mt-5 text-center text-xs text-neutral-500">
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
