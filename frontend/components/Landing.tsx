import {
  FileStack, FileText, FileImage, Mic, Video, Globe, ArrowDown,
  Search, ShieldCheck, MessageSquare, AlertTriangle, CalendarClock,
  Route, Lock, ListChecks, Upload, Layers,
} from 'lucide-react'
import RedirectIfAuthenticated from '@/components/RedirectIfAuthenticated'
import AuthForm from '@/components/landing/AuthForm'
import GuestEntry from '@/components/landing/GuestEntry'
import { LandingLocaleProvider } from '@/components/landing/LandingLocale'
import SectionIndex from '@/components/landing/SectionIndex'

const SOURCES = [
  { icon: FileText, name: 'PDFs' },
  { icon: FileImage, name: 'Images' },
  { icon: Mic, name: 'Audio' },
  { icon: Video, name: 'Video' },
  { icon: Globe, name: 'Web pages' },
]

const CAPABILITIES = [
  {
    icon: AlertTriangle,
    title: 'See when sources disagree',
    description: 'Older support tickets may quote a policy that has since changed. BrainHub can flag conflicting passages and show their document dates so you can review the evidence. A newer date identifies a more recent source; it does not establish which document governs.',
  },
  {
    icon: CalendarClock,
    title: 'Look back at an earlier date',
    description: 'Use “Answer as of” to search documents dated on or before a chosen day. This helps investigate what information was available before a revision. The filter uses the supplied document date, or the upload date when none was supplied.',
  },
  {
    icon: Route,
    title: 'Understand where an answer came from',
    description: 'Open the citations to inspect the source passages. The decision trail also shows what was searched, which passages were selected and whether the evidence was marked low confidence. You can review the retrieval behind a response instead of relying on its wording alone.',
  },
]

const PIPELINE = [
  {
    icon: Upload,
    title: 'Bring different sources together',
    description: 'PDFs, images, audio files, videos and imported web pages become searchable material in one workspace. Audio files are transcribed. Video answers use descriptions of six sampled visual frames; they do not cover every scene or transcribe the video’s soundtrack.',
    reason: 'One question can draw on more than one source format.',
  },
  {
    icon: Layers,
    title: 'Index meaning alongside keywords',
    description: 'Multimodal embeddings place text, images and video in a shared search space. A keyword index runs alongside the semantic index, helping retrieve exact terms as well as related concepts.',
    reason: 'Find relevant evidence even when the question uses different words.',
  },
  {
    icon: Search,
    title: 'Search and rank the evidence',
    description: 'The system searches variations of the question, combines semantic and keyword results, then reranks the candidate passages when the reranking service is available.',
    reason: 'Select passages for the question before asking the model to answer.',
  },
  {
    icon: ShieldCheck,
    title: 'Recognize weak evidence',
    description: 'If no passage clears the relevance check, the workflow rewrites the question and searches again. If evidence remains weak, the response is marked low confidence. Conflict detection can also miss disagreements; the source passages remain available for review.',
    reason: 'Make uncertainty visible when the library does not support a clear answer.',
  },
  {
    icon: MessageSquare,
    title: 'Answer with the supporting passages',
    description: 'The model receives the selected evidence with instructions to cite its sources and say when they do not contain an answer. The response streams into the conversation together with citations and the retrieval trail.',
    reason: 'Keep the answer connected to evidence you can inspect.',
  },
]

const SAFEGUARDS = [
  {
    icon: Lock,
    title: 'Account boundaries apply to retrieval',
    description: 'Document access, searches, conversations and decision trails are scoped to the account. Guest sessions can read the fictional sample library and ask questions; they cannot upload or change its documents.',
  },
  {
    icon: ListChecks,
    title: 'Ingestion continues in the background',
    description: 'A queue separates document processing from the browser request. The workspace shows processing status while workers prepare the sources for search, so a larger file does not depend on keeping an upload request open.',
  },
  {
    icon: Upload,
    title: 'Uploads are checked before processing',
    description: 'The server checks file content rather than trusting the filename, restricts supported formats and enforces the upload size limit while receiving the file.',
  },
  {
    icon: Globe,
    title: 'External search is an explicit choice',
    description: 'Web search is disabled by default. When enabled, it can provide a fallback after the library search finds weak evidence. External results are identified as web sources, and this fallback is not used with a historical date filter.',
  },
]

const ARCHITECTURE = [
  { title: 'Next.js and FastAPI', description: 'The web interface handles the library and conversation; the API coordinates ingestion, retrieval and streamed responses.' },
  { title: 'PostgreSQL and pgvector', description: 'Documents, conversations, keyword search and vector retrieval share a persistence layer with explicit account scoping.' },
  { title: 'Specialized retrieval and language models', description: 'Voyage embeds the source material, Cohere reranks passages, and the configured language model generates answers from the selected evidence.' },
  { title: 'Redis and background workers', description: 'Queued ingestion, operational limits and scheduled guest cleanup run outside the browser interaction.' },
]

export default function Landing() {
  return (
    <LandingLocaleProvider>
      <RedirectIfAuthenticated to="/library" />
      <main className="relative text-white">
        <header className="sticky top-0 z-50 border-b border-white/10 glass-panel">
          <div className="max-w-7xl mx-auto flex h-14 items-center justify-between px-4 sm:px-8">
            <a href="#index" className="flex items-center gap-2.5 font-semibold tracking-tight rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-300">
              <FileStack className="h-5 w-5" aria-hidden /> BrainHub
            </a>
            <a href="#auth" className="text-sm font-medium text-neutral-900 bg-white hover:bg-neutral-100 px-4 py-1.5 rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-300">
              Sign in
            </a>
          </div>
        </header>
        <SectionIndex />

        <section id="index" className="max-w-7xl mx-auto px-4 sm:px-8 pt-12 pb-16 md:pt-20 md:pb-20 scroll-mt-20">
          <div className="grid lg:grid-cols-12 gap-10 lg:gap-12 items-center">
            <div className="lg:col-span-7">
              <p className="text-sm text-blue-200 mb-5">A knowledge workspace with answers you can check</p>
              <h1 className="text-4xl sm:text-5xl md:text-6xl font-semibold tracking-tighter leading-[1.05] max-w-2xl">
                Ask across your files.<br className="hidden sm:block" /> See the sources behind the answer.
              </h1>
              <p className="mt-6 text-base sm:text-lg text-neutral-300 leading-relaxed max-w-xl">
                Search documents, images, recordings and web pages in one place. BrainHub brings the relevant passages into the answer and helps you review conflicting information and document dates.
              </p>
              <div className="mt-6 mb-7 flex flex-wrap gap-x-4 gap-y-2">
                {SOURCES.map(({ icon: Icon, name }) => (
                  <span key={name} className="inline-flex items-center gap-1.5 text-xs text-neutral-300">
                    <Icon className="h-3.5 w-3.5 text-blue-300" aria-hidden />{name}
                  </span>
                ))}
              </div>
              <GuestEntry />
              <a href="#different" className="inline-flex items-center gap-2 mt-5 text-sm text-neutral-300 underline underline-offset-4 decoration-white/25 hover:text-white">
                Explore the capabilities <ArrowDown className="h-3.5 w-3.5" aria-hidden />
              </a>
            </div>
            <div className="lg:col-span-5">
              <SampleAnswer />
            </div>
          </div>
        </section>

        <section aria-labelledby="demo-guide" className="border-y border-white/10 bg-white/[0.02]">
          <div className="max-w-7xl mx-auto px-4 sm:px-8 py-10 sm:py-12">
            <h2 id="demo-guide" className="text-xl font-semibold tracking-tight">Three things to try in the sample library</h2>
            <p className="mt-2 text-sm text-neutral-400 max-w-2xl">The fictional library includes two versions of a shipping policy and support records that refer to them.</p>
            <ol className="mt-7 grid md:grid-cols-3 gap-7 md:gap-10">
              <li><h3 className="font-medium text-blue-200">Ask about the policy</h3><p className="mt-2 text-sm text-neutral-300 leading-relaxed">“From what order value is national shipping free?” Compare the answer with the two cited policy versions.</p></li>
              <li><h3 className="font-medium text-blue-200">Look back in time</h3><p className="mt-2 text-sm text-neutral-300 leading-relaxed">Set “Answer as of” to March 31, 2025, then ask the same question. The later policy should be excluded from the search.</p></li>
              <li><h3 className="font-medium text-blue-200">Inspect the evidence</h3><p className="mt-2 text-sm text-neutral-300 leading-relaxed">Open a citation and “Why these answers?” to review the source passages and the path used to select them.</p></li>
            </ol>
            <p className="mt-7 text-xs text-neutral-400 leading-relaxed max-w-3xl">The demo is read-only and uses fictional documents. Guest access is temporary; chat history is scheduled for deletion after the session expires. Use an account to upload your own sources.</p>
          </div>
        </section>

        <section id="different" className="max-w-7xl mx-auto px-4 sm:px-8 py-16 sm:py-20 scroll-mt-16">
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight max-w-2xl">Review the evidence behind a decision.</h2>
          <p className="mt-4 max-w-2xl text-neutral-400 leading-relaxed">Finding a passage is one part of the task. Understanding its context, date and relationship to other sources helps you decide how to use the answer.</p>
          <div className="mt-10 grid md:grid-cols-3 gap-6">
            {CAPABILITIES.map(({ icon: Icon, title, description }) => (
              <article key={title} className="border-t border-white/15 pt-6">
                <Icon className="h-5 w-5 text-blue-300 mb-4" aria-hidden />
                <h3 className="text-lg font-semibold tracking-tight">{title}</h3>
                <p className="mt-3 text-sm text-neutral-400 leading-relaxed">{description}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="pipeline" className="border-t border-white/10 scroll-mt-16">
          <div className="max-w-7xl mx-auto px-4 sm:px-8 py-16 sm:py-20">
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">How a question becomes an answer.</h2>
            <p className="mt-4 max-w-2xl text-neutral-400 leading-relaxed">A retrieval workflow prepares and selects evidence before the language model writes its response.</p>
            <ol className="mt-9">
              {PIPELINE.map(({ icon: Icon, title, description, reason }, index) => (
                <li key={title} className="grid sm:grid-cols-12 gap-4 sm:gap-8 border-t border-white/10 py-7 first:border-t-0">
                  <div className="sm:col-span-1 flex items-center gap-3 text-blue-200"><span className="text-sm">{index + 1}</span><Icon className="h-5 w-5" aria-hidden /></div>
                  <div className="sm:col-span-7"><h3 className="text-lg font-semibold">{title}</h3><p className="mt-2 text-sm text-neutral-400 leading-relaxed">{description}</p></div>
                  <p className="sm:col-span-4 text-sm text-blue-100/80 leading-relaxed sm:pt-1">{reason}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="features" className="border-t border-white/10 scroll-mt-16">
          <div className="max-w-7xl mx-auto px-4 sm:px-8 py-16 sm:py-20">
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">Built around everyday constraints.</h2>
            <div className="mt-10 grid md:grid-cols-2 gap-x-12 gap-y-8">
              {SAFEGUARDS.map(({ icon: Icon, title, description }) => (
                <article key={title} className="border-t border-white/10 pt-6">
                  <Icon className="h-5 w-5 text-blue-300 mb-4" aria-hidden />
                  <h3 className="text-xl font-semibold tracking-tight">{title}</h3>
                  <p className="mt-3 text-sm text-neutral-400 leading-relaxed max-w-xl">{description}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="stack" className="border-t border-white/10 bg-white/[0.02] scroll-mt-16">
          <div className="max-w-7xl mx-auto px-4 sm:px-8 py-12 sm:py-16">
            <h2 className="text-2xl sm:text-3xl font-semibold tracking-tight">Architecture and the reasons behind it.</h2>
            <dl className="mt-8 grid sm:grid-cols-2 gap-x-12 gap-y-7">
              {ARCHITECTURE.map(({ title, description }) => <div key={title}><dt className="font-medium text-blue-100">{title}</dt><dd className="mt-2 text-sm text-neutral-400 leading-relaxed">{description}</dd></div>)}
            </dl>
          </div>
        </section>

        <section id="auth" className="border-t border-white/10 scroll-mt-20">
          <div className="max-w-6xl mx-auto px-4 sm:px-8 py-16 sm:py-20 grid md:grid-cols-12 gap-10 lg:gap-14 items-start">
            <div className="md:col-span-7 md:pt-6">
              <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">Build your own knowledge workspace.</h2>
              <p className="mt-5 text-neutral-400 leading-relaxed max-w-lg">Sign in or create an account to upload sources, import web pages and keep your conversations in a workspace scoped to your account.</p>
              <a href="#index" className="mt-6 inline-block text-sm text-blue-200 underline underline-offset-4">Explore the sample library without an account</a>
            </div>
            <div className="md:col-span-5"><AuthForm /></div>
          </div>
        </section>
      </main>
    </LandingLocaleProvider>
  )
}

function SampleAnswer() {
  return (
    <figure className="rounded-3xl bg-white/[0.04] ring-1 ring-white/10 p-5 sm:p-6">
      <figcaption className="text-xs text-neutral-400 mb-5">Example answer from the fictional demo library</figcaption>
      <p className="ml-6 rounded-2xl rounded-tr-sm bg-white text-neutral-900 px-4 py-3 text-sm leading-relaxed">From what order value is national shipping free?</p>
      <div className="mt-5 rounded-2xl rounded-tl-sm bg-white/5 ring-1 ring-white/10 px-4 py-3 text-sm text-neutral-200 leading-relaxed">
        Version 2 of the commercial policy says above R$150. Version 1 says R$120, and older support tickets still quote that amount. Review the policy documents before deciding which applies.
      </div>
      <div className="mt-4 rounded-xl border border-amber-400/25 bg-amber-400/[0.06] p-3 text-xs leading-relaxed">
        <p className="flex items-center gap-2 font-medium text-amber-200"><AlertTriangle className="h-4 w-4 shrink-0" aria-hidden /> The sources give different thresholds</p>
        <p className="mt-2 text-neutral-300">Version 2 has the more recent document date. That date alone does not determine which policy applies.</p>
      </div>
      <ul className="mt-4 space-y-2 text-xs text-blue-200" aria-label="Example source references">
        <li className="flex items-center gap-2"><FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />Política Comercial, versão 2 · page 1</li>
        <li className="flex items-center gap-2"><FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />Política Comercial, versão 1 · page 1</li>
      </ul>
      <p className="mt-5 pt-4 border-t border-white/10 text-xs text-neutral-400 leading-relaxed">Illustrative preview. Open the demo to ask your own question and inspect the original sources.</p>
    </figure>
  )
}
