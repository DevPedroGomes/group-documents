// Componente de SERVIDOR de proposito. A landing inteira era um componente de
// cliente que, enquanto a sessao era checada, renderizava so "checking
// session": o HTML que chegava a um crawler (e a quem abria o link no
// LinkedIn) nao tinha nenhuma linha do conteudo. Agora so tres ilhas rodam no
// cliente: o idioma, o formulario de login e o redirecionamento de quem ja
// tem sessao para /library.
//
// Cada afirmacao tecnica daqui foi conferida contra o backend. Numero que o
// codigo nao produz (latencia P95, por exemplo) nao entra.

import {
  FileStack, FileText, Cpu, Search, ShieldCheck, MessageSquare,
  Globe, ArrowRight, Users, Lock, FileImage, Mic, Video,
  Sparkles, CheckCircle2, Bot, User, ArrowDown, AlertTriangle,
  CalendarClock, Route, X,
} from 'lucide-react'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import RedirectIfAuthenticated from '@/components/RedirectIfAuthenticated'
import AuthForm from '@/components/landing/AuthForm'
import { LandingLocaleProvider, LocaleToggle, T } from '@/components/landing/LandingLocale'

const SECTION_IDS = ['index', 'different', 'pipeline', 'features', 'stack'] as const

const PIPELINE = [
  {
    icon: FileText,
    title: 'Multi-modal ingest',
    desc: 'PDF text is read with pypdf; pages with no text layer are rendered as images and embedded as such. Images and video are embedded directly by Voyage, audio is transcribed by Deepgram. libmagic sniffs MIME from the bytes, not the extension.',
    trace: 'mime: application/pdf · 4.2MB · 287 chunks',
  },
  {
    icon: Cpu,
    title: 'Embed & index',
    desc: 'One multimodal Voyage model embeds documents and queries, so text, images and video share a single vector space. A Postgres tsvector index is kept alongside for keyword search.',
    trace: 'voyage-multimodal-3.5 · 1024d · pgvector HNSW',
  },
  {
    icon: Search,
    title: 'Hybrid retrieval',
    desc: 'Each question becomes a few search queries. For each one, a pgvector search and a Postgres full-text search are fused with Reciprocal Rank Fusion; a Cohere reranker then orders the pool and keeps the top five.',
    trace: 'rrf_k=60 · top_k=5 · rerank=5',
  },
  {
    icon: ShieldCheck,
    title: 'Relevance gate',
    desc: 'The reranker scores each passage from 0 to 1 and only passages at 0.7 or above go to the answer. If none clears the bar, the question is rewritten once and the library searched again; both result sets are merged and graded again.',
    trace: 'cohere ≥ 0.7 · rewrite + search again',
  },
  {
    icon: MessageSquare,
    title: 'Grounded synthesis',
    desc: 'Claude Sonnet answers from the retrieved passages only, cites document and page, and says it did not find the answer instead of filling the gap from general knowledge. Tokens stream as they are generated.',
    trace: 'claude-sonnet-5 · stream=true',
  },
]

const MODALITIES = [
  { icon: FileText,  name: 'PDFs' },
  { icon: FileImage, name: 'Images' },
  { icon: Mic,       name: 'Audio' },
  { icon: Video,     name: 'Video' },
]

// So numeros que saem da configuracao do codigo. O "<2s P95" que estava aqui
// nunca foi medido.
const STATS = [
  { value: '5',     label: 'Pipeline stages' },
  { value: '4',     label: 'Modalities' },
  { value: '1024',  label: 'Vector dimensions' },
  { value: '3',     label: 'Query variants per question' },
]

const DIFFERENTIATORS = [
  {
    icon: AlertTriangle,
    label: 'A / Disagreement',
    title: 'Sources that disagree, and which one is current',
    desc: 'When the passages behind an answer come from two or more documents, a second model call checks whether they conflict. If they do, the answer carries the warning and names the documents; the one with the most recent document date is marked current. That date comparison is plain code, not the model.',
  },
  {
    icon: CalendarClock,
    label: 'B / Time',
    title: 'Answer as of a date',
    desc: 'Pick a date and only documents dated on or before it are searched: the date set at upload, or the upload day when none was set. Ask what the policy said in March 2025 and the 2026 revision stays out of the answer.',
  },
  {
    icon: Route,
    label: 'C / Trail',
    title: 'The decision trail',
    desc: 'Every answer is saved with the path that produced it. “Why these answers?” shows the queries actually searched, each passage retrieved with its score and scale, which ones were kept, and whether the answer was flagged low confidence.',
  },
]

const FEATURES = [
  {
    icon: Lock,
    label: '01 / Isolation',
    title: 'Tenant-isolated retrieval',
    desc: 'Every chunk row carries a user_id; vector and keyword queries WHERE-filter on it before fusion or rerank. No cross-tenant path exists at the SQL layer.',
  },
  {
    icon: Sparkles,
    label: '02 / LLM',
    title: 'Multi-provider routing',
    desc: 'Anthropic native or any OpenRouter model, chosen by env var. Query generation, the disagreement check and the answer all go through that one client; embeddings and reranking stay on Voyage and Cohere.',
  },
  {
    icon: Globe,
    label: '03 / Fallback',
    title: 'Web search is opt-in',
    desc: 'Sending a question to a third party is off by default. When enabled, a Tavily search runs only after the rewritten query still finds nothing, never with a date cut-off, and web results are marked as web wherever they appear.',
  },
  {
    icon: CheckCircle2,
    label: '04 / Citations',
    title: 'Answers show their passages',
    desc: 'Each answer lists the passages it was generated from, with document, page, date and a snippet; a click opens the file at that page. The prompt answers only from those passages and says so when they do not contain the answer.',
  },
  {
    icon: Users,
    label: '05 / Workspace',
    title: 'Private workspace per account',
    desc: 'Documents, retrieval, chat threads and decision trails are scoped to the account that created them.',
  },
  {
    icon: ShieldCheck,
    label: '06 / Upload',
    title: 'libmagic byte sniffing',
    desc: 'MIME is read from bytes, not file extension. Allowlist enforced server-side; renamed payloads are rejected before they touch storage, and the size limit is counted while the upload streams in.',
  },
]

const STACK = [
  'Claude Sonnet',
  'Voyage multimodal-3.5',
  'Cohere Rerank',
  'pgvector HNSW',
  'PostgreSQL',
  'Redis 7',
  'arq',
  'FastAPI',
  'Next.js 16',
  'React 19',
  'Turbopack',
  'OpenAI Realtime',
  'Deepgram Nova-3',
  'PyMuPDF',
  'libmagic',
]

export default function Landing() {
  return (
    <LandingLocaleProvider>
      {/* Quem ja tem sessao vai para a biblioteca. E a unica parte que precisa
          do navegador: a sessao vive no localStorage. */}
      <RedirectIfAuthenticated to="/library" />

      <main className="relative text-white">
        {/* ─── Top bar ─────────────────────────────────────────────────── */}
        <header className="sticky top-0 z-50 w-full border-b border-white/10 glass-panel">
          <div className="max-w-7xl mx-auto flex h-14 items-center justify-between px-4 sm:px-8">
            <div className="flex items-center gap-2.5">
              <div
                className="flex h-8 w-8 items-center justify-center rounded-full bg-white/10 backdrop-blur border-gradient"
                style={{ borderRadius: 9999 }}
              >
                <FileStack className="h-4 w-4 text-white" />
              </div>
              <span className="font-semibold tracking-tight text-white">BrainHub</span>
              <span className="hidden sm:inline-block text-[11px] font-mono text-neutral-500 ml-1">/ rag</span>
            </div>
            <div className="flex items-center gap-2">
              <LocaleToggle />
              <a
                href="#auth"
                className="text-sm font-medium text-neutral-900 bg-white hover:bg-neutral-100 transition-colors px-4 py-1.5 rounded-full"
              >
                <T k="nav.signIn" />
              </a>
            </div>
          </div>
        </header>

        {/* ─── Sticky section index (lg+) ─────────────────────────────── */}
        <nav
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
              <span className="opacity-0 group-hover:opacity-100 transition-opacity">
                <T k={`sections.${id}`} />
              </span>
            </a>
          ))}
        </nav>

        {/* ─── 01 · Asymmetric hero with chat preview ─────────────────── */}
        <section
          id="index"
          className="max-w-7xl mx-auto px-4 sm:px-8 pt-16 pb-20 md:pt-20 md:pb-24"
        >
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 lg:gap-12 items-center">
            {/* Left column: text */}
            <div className="lg:col-span-7">
              <div className="flex items-center gap-3 mb-8">
                <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                  <T k="hero.tag" />
                </span>
                <span className="h-px flex-1 max-w-[80px] bg-white/10" />
                <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500">
                  <T k="hero.version" />
                </span>
              </div>

              <h1 className="text-5xl sm:text-6xl md:text-7xl font-semibold tracking-tighter leading-[0.95] text-white mb-7">
                <T k="hero.title1" />
                <br />
                <T k="hero.title2" />
                <br />
                <span className="gradient-text"><T k="hero.title3" /></span>
              </h1>

              <p className="text-base sm:text-lg text-neutral-400 leading-relaxed max-w-xl mb-7">
                <T k="hero.subtitle" />
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
                <a href="#auth" className={cn(buttonVariants({ size: 'lg' }), 'rounded-full px-7 gap-2')}>
                  <T k="hero.cta.open" /> <ArrowRight className="h-4 w-4" />
                </a>
                <a
                  href="#different"
                  className={cn(buttonVariants({ size: 'lg', variant: 'outline' }), 'rounded-full px-7 gap-2')}
                >
                  <T k="hero.cta.read" /> <ArrowDown className="h-4 w-4" />
                </a>
              </div>
            </div>

            {/* Right column: an example answer, with what the chat really shows */}
            <div className="lg:col-span-5 lg:pl-4">
              <div
                className="relative rounded-3xl bg-white/[0.04] ring-1 ring-white/10 border-gradient backdrop-blur p-5 sm:p-6 fade-slide-in"
                style={{ borderRadius: 24 }}
              >
                <div className="flex items-center justify-between text-[10px] font-mono text-neutral-500 mb-5">
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-blue-400/70" />
                    <span className="uppercase tracking-widest">example answer</span>
                  </div>
                  <span>thread / 8a3f</span>
                </div>

                <div className="flex justify-end mb-4">
                  <div className="max-w-[85%] flex items-start gap-2.5 flex-row-reverse">
                    <div className="h-7 w-7 rounded-full bg-white shrink-0 flex items-center justify-center">
                      <User className="h-3.5 w-3.5 text-neutral-900" />
                    </div>
                    <div className="rounded-2xl rounded-tr-sm bg-white text-neutral-900 px-3.5 py-2 text-sm">
                      How long do customers have to ask for a refund?
                    </div>
                  </div>
                </div>

                <div className="flex items-start gap-2.5 mb-3">
                  <div className="h-7 w-7 rounded-full bg-white/5 ring-1 ring-white/10 shrink-0 flex items-center justify-center">
                    <Bot className="h-3.5 w-3.5 text-blue-300" />
                  </div>
                  <div className="flex-1 min-w-0 max-w-[88%]">
                    <div className="rounded-2xl rounded-tl-sm bg-white/5 ring-1 ring-white/10 px-3.5 py-2.5 text-sm text-neutral-100 leading-relaxed">
                      <span className="text-blue-300">30 days</span> under the 2026 pricing policy (p. 3).
                      The 2025 policy allowed 14 days, so older receipts may follow the old rule.
                    </div>

                    <div className="mt-2.5 rounded-xl border border-amber-400/30 bg-amber-400/[0.07] px-3 py-2 text-[11px]">
                      <p className="flex items-center gap-1.5 font-semibold text-amber-200">
                        <AlertTriangle className="h-3 w-3" aria-hidden /> Your sources disagree
                      </p>
                      <p className="mt-1 text-neutral-300">
                        <span className="text-emerald-200">Pricing-2026.pdf</span> is current: it has the
                        most recent document date.
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-1.5 mt-2.5">
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-400/15 border border-blue-400/30 text-[10px] text-blue-200">
                        <FileText className="h-3 w-3" />
                        <span className="max-w-[120px] truncate">Pricing-2026.pdf</span>
                        <span className="text-blue-300/80 font-mono">p.3</span>
                      </span>
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-400/15 border border-blue-400/30 text-[10px] text-blue-200">
                        <FileText className="h-3 w-3" />
                        <span className="max-w-[120px] truncate">Pricing-2025.pdf</span>
                        <span className="text-blue-300/80 font-mono">p.2</span>
                      </span>
                    </div>
                  </div>
                </div>

                <div className="mt-5 pt-3 border-t border-white/5 flex items-center justify-between gap-3 text-[10px] font-mono text-neutral-500">
                  <span>5 retrieved → 2 kept</span>
                  <span className="inline-flex items-center gap-1 text-blue-300">
                    <Route className="h-3 w-3" aria-hidden /> trail saved
                  </span>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ─── Stat band ───────────────────────────────────────────────── */}
        <section className="border-y border-white/10 glass-panel">
          <div className="max-w-7xl mx-auto px-4 sm:px-8 py-10 sm:py-12">
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

        {/* ─── 02 · What comes after the citation ─────────────────────── */}
        <section id="different" className="max-w-7xl mx-auto px-4 sm:px-8 py-20 sm:py-24">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 mb-12">
            <div className="lg:col-span-4">
              <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                02 / Beyond citations
              </span>
              <h2 className="mt-3 text-3xl sm:text-4xl font-semibold tracking-tighter text-white">
                Cited is table stakes.
                <br />
                <span className="text-neutral-500">Here is what&apos;s next.</span>
              </h2>
            </div>
            <div className="lg:col-span-8 lg:pt-10">
              <p className="text-neutral-400 text-base leading-relaxed max-w-2xl">
                A citation says where a sentence came from. It does not say whether another
                document says the opposite, whether the answer would have been different last
                year, or what was left out. These three do, and the chat shows each one next to the
                answer it belongs to.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {DIFFERENTIATORS.map((d, i) => (
              <article
                key={d.title}
                className="flex flex-col rounded-2xl bg-white/[0.03] ring-1 ring-white/10 p-5 sm:p-6"
              >
                <div className="flex items-center gap-3 mb-4">
                  <span className="inline-flex items-center justify-center h-8 w-8 rounded-lg bg-white/5 ring-1 ring-white/10 text-blue-300">
                    <d.icon className="h-4 w-4" strokeWidth={1.8} />
                  </span>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500">
                    {d.label}
                  </span>
                </div>
                <h3 className="text-lg font-semibold text-white tracking-tight">{d.title}</h3>
                <p className="mt-2 mb-5 text-sm text-neutral-400 leading-relaxed">{d.desc}</p>
                <div className="mt-auto pt-4 border-t border-white/5">
                  <p className="mb-2 text-[9px] font-mono uppercase tracking-widest text-neutral-600">
                    in the chat
                  </p>
                  {i === 0 && <AmostraDivergencia />}
                  {i === 1 && <AmostraRecorte />}
                  {i === 2 && <AmostraTrilha />}
                </div>
              </article>
            ))}
          </div>
        </section>

        {/* ─── 03 · Pipeline as vertical timeline ──────────────────────── */}
        <section
          id="pipeline"
          className="border-t border-white/10"
        >
          <div className="max-w-7xl mx-auto px-4 sm:px-8 py-20 sm:py-24">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 mb-12">
              <div className="lg:col-span-4">
                <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                  03 / Pipeline
                </span>
                <h2 className="mt-3 text-3xl sm:text-4xl font-semibold tracking-tighter text-white">
                  Five stages.
                  <br />
                  <span className="text-neutral-500">No shortcuts.</span>
                </h2>
              </div>
              <div className="lg:col-span-8 lg:pt-10">
                <p className="text-neutral-400 text-base leading-relaxed max-w-2xl">
                  Every question follows the same path. When nothing clears the relevance bar, the
                  question is rewritten and searched once more. If that still fails, the model is told
                  the passages may not answer it, and the answer is marked low confidence.
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

                  <div className="sm:col-span-3 flex sm:justify-end items-start sm:pt-1 min-w-0">
                    <span className="trace-chip max-w-full !whitespace-normal !rounded-xl sm:text-right">{step.trace}</span>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* ─── 04 · Editorial features (zigzag, no cards) ──────────────── */}
        <section
          id="features"
          className="border-t border-white/10"
        >
          <div className="max-w-7xl mx-auto px-4 sm:px-8 py-20 sm:py-24">
            <div className="flex items-end justify-between mb-12 gap-6">
              <div>
                <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                  04 / Engineering
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
                    className={`relative py-8 sm:py-10 px-2 sm:px-8 ${
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
          <div className="max-w-7xl mx-auto px-4 sm:px-8 mb-6">
            <div className="flex items-baseline gap-3">
              <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                05 / Stack
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
                  aria-hidden={i >= STACK.length ? true : undefined}
                >
                  {tech}
                  <span className="text-blue-400/40 text-base">●</span>
                </span>
              ))}
            </div>
          </div>
        </section>

        {/* ─── Auth (CTA + form, asymmetric split) ────────────────────── */}
        <section id="auth" className="border-t border-white/10">
          <div className="max-w-6xl mx-auto px-4 sm:px-8 py-20 sm:py-24">
            <div className="grid grid-cols-1 md:grid-cols-12 gap-10 lg:gap-14 items-start">
              {/* Left: editorial CTA copy */}
              <div className="md:col-span-7 md:pt-6">
                <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                  Get started
                </span>
                <h2 className="mt-3 text-4xl sm:text-5xl md:text-6xl font-semibold tracking-tighter leading-[1.0] text-white">
                  Stop grepping
                  <br />
                  your own files.
                </h2>
                <p className="mt-5 text-neutral-400 text-base max-w-lg leading-relaxed">
                  Drop what you have into one workspace and ask. Every answer shows the passages
                  it came from, and the path it took to find them.
                </p>

                {/* terminal flourish */}
                <div className="mt-12 flex items-center gap-3 text-[11px] font-mono text-neutral-500">
                  <span className="text-blue-300">$</span>
                  <span>brainhub</span>
                  <span className="text-neutral-700">init</span>
                  <span className="text-neutral-700">--workspace</span>
                  <span className="text-blue-300">my-workspace</span>
                  <span className="ml-1 inline-block w-2 h-3.5 bg-blue-300/80 animate-pulse" />
                </div>
              </div>

              {/* Right: auth form */}
              <div className="md:col-span-5">
                <AuthForm />
              </div>
            </div>
          </div>
        </section>
      </main>
    </LandingLocaleProvider>
  )
}

// Amostras estaticas do que o chat mostra: mesma cor e forma dos componentes
// reais (ChatMessage, ChatInput, DecisionTrail), com dados de exemplo.

function AmostraDivergencia() {
  return (
    <div className="rounded-xl border border-amber-400/30 bg-amber-400/[0.07] px-3 py-2.5 text-[11px]">
      <p className="flex items-center gap-1.5 font-semibold text-amber-200">
        <AlertTriangle className="h-3 w-3" aria-hidden /> Your sources disagree
      </p>
      <p className="mt-1 text-neutral-300">Refund window: 14 days in 2025, 30 days in 2026.</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="rounded-full bg-white/5 px-2 py-0.5 text-neutral-300 ring-1 ring-white/10">
          Pricing-2025.pdf
        </span>
        <span className="rounded-full bg-emerald-400/10 px-2 py-0.5 text-emerald-200 ring-1 ring-emerald-400/30">
          Pricing-2026.pdf · current
        </span>
      </div>
    </div>
  )
}

function AmostraRecorte() {
  return (
    <div className="space-y-2 text-[11px]">
      <div className="inline-flex items-center gap-2 rounded-full bg-blue-400/10 py-1 pl-3 pr-1.5 ring-1 ring-blue-400/40">
        <CalendarClock className="h-3.5 w-3.5 text-blue-300" aria-hidden />
        <span className="text-neutral-300">Answer as of</span>
        <span className="font-mono text-white">2025-03-31</span>
        <span className="flex h-5 w-5 items-center justify-center rounded-full text-neutral-400">
          <X className="h-3 w-3" aria-hidden />
        </span>
      </div>
      <ul className="space-y-1 font-mono text-[10px]">
        <li className="flex items-center justify-between gap-2 text-neutral-300">
          <span className="truncate">Pricing-2025.pdf</span>
          <span className="text-emerald-300">dated 2025-01-15 · in</span>
        </li>
        <li className="flex items-center justify-between gap-2 text-neutral-500">
          <span className="truncate line-through decoration-neutral-600">Pricing-2026.pdf</span>
          <span>dated 2026-02-01 · out</span>
        </li>
      </ul>
    </div>
  )
}

function AmostraTrilha() {
  return (
    <div className="space-y-2 text-[11px]">
      <p className="text-neutral-400">
        3 retrieved → <span className="text-neutral-200">2 kept</span>
        <span className="ml-2 rounded bg-white/5 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-neutral-300">
          reranked
        </span>
      </p>
      <ul className="divide-y divide-white/5 rounded-md font-mono text-[10px] ring-1 ring-white/5">
        <li className="flex items-center justify-between gap-2 px-2 py-1 text-neutral-300">
          <span className="truncate">Pricing-2026.pdf p.3</span>
          <span>0.91 · <span className="text-emerald-300">kept</span></span>
        </li>
        <li className="flex items-center justify-between gap-2 px-2 py-1 text-neutral-300">
          <span className="truncate">Pricing-2025.pdf p.2</span>
          <span>0.84 · <span className="text-emerald-300">kept</span></span>
        </li>
        <li className="flex items-center justify-between gap-2 px-2 py-1 text-neutral-500">
          <span className="truncate">Handbook.pdf p.7</span>
          <span>0.31 · dropped</span>
        </li>
      </ul>
    </div>
  )
}
