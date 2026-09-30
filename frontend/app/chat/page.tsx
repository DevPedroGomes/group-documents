'use client'

import { useCallback, useEffect, useMemo, useRef, useState, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { useChatStream } from '@/hooks/useChatStream'
import { ChatHeader } from '@/components/chat/ChatHeader'
import { ChatHistory } from '@/components/chat/ChatHistory'
import { ChatMessage, ThinkingMessage } from '@/components/chat/ChatMessage'
import { ChatInput } from '@/components/chat/ChatInput'
import { LiveVoice } from '@/components/chat/LiveVoice'
import { DecisionTrail } from '@/components/chat/DecisionTrail'
import { DocumentPreview } from '@/components/DocumentPreview'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { AnimatePresence, motion } from 'framer-motion'
import { Loader2, ArrowRight } from 'lucide-react'
import { lerEscopo, salvarEscopo } from '@/lib/thread-scope'
import type { Citation, DoneEvent, LibraryDocument } from '@/lib/types'

function ChatPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const scrollRef = useRef<HTMLDivElement>(null)
  const { user, loading, getToken } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const [asOf, setAsOf] = useState('')
  const [historicoAberto, setHistoricoAberto] = useState(false)
  /** O acervo; `null` enquanto nao carregou (ou se falhou): nada e bloqueado por isso. */
  const [acervo, setAcervo] = useState<LibraryDocument[] | null>(null)
  const [citacaoAberta, setCitacaoAberta] = useState<Citation | null>(null)

  // Selecao vinda da biblioteca (?docs=). Vazia = a pergunta vai para o acervo inteiro.
  const docsParam = searchParams.get('docs') ?? ''
  const documentIds = useMemo(() => docsParam.split(',').filter(Boolean), [docsParam])

  // Redirect to home if not authenticated
  useEffect(() => {
    if (!loading && !user) {
      router.push('/')
    }
  }, [loading, user, router])

  // Estavel de proposito: recriada a cada render, ela fazia o historico ser
  // buscado de novo a cada token do stream.
  const getTokenAsync = useCallback(async () => getToken() ?? undefined, [getToken])

  // O acervo diz se ha o que perguntar e o tipo de cada documento citado,
  // que o preview precisa para abrir o PDF na pagina certa.
  useEffect(() => {
    if (!user) return
    let cancelado = false
    ;(async () => {
      try {
        const token = await getTokenAsync()
        const res = await fetch('/api/documents', {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok) return
        const data = (await res.json()) as { items?: LibraryDocument[] }
        if (!cancelado) setAcervo(data.items ?? [])
      } catch {
        // Sem a lista o chat segue funcionando; so nao sabe se o acervo esta vazio.
      }
    })()
    return () => {
      cancelado = true
    }
  }, [user, getTokenAsync])

  const aoTerminarResposta = useCallback((done: DoneEvent, escopo: string[] | undefined) => {
    if (done.thread_id) salvarEscopo(done.thread_id, escopo ?? [])
  }, [])

  const {
    messages,
    isLoading: isSending,
    sendMessage,
    resetChat,
    stopGeneration,
    workflowSteps,
    threadId,
    loadThread,
    answerCount,
  } = useChatStream({
    getToken: getTokenAsync,
    documentIds,
    asOf,
    onError: setError,
    onDone: aoTerminarResposta,
  })

  // Reabrir uma conversa restaura o escopo dela. Sem registro neste navegador,
  // ela continua no acervo inteiro, com o campo habilitado.
  const selecionarThread = useCallback(
    async (id: string) => {
      setError(null)
      const ok = await loadThread(id)
      if (!ok) {
        setError('Could not open that conversation.')
        return
      }
      const salvo = lerEscopo(id) ?? []
      const escopo = acervo ? salvo.filter((docId) => acervo.some((d) => d.id === docId)) : salvo
      // `history.replaceState` e integrado ao router do Next e atualiza o
      // `useSearchParams` sem ir ao servidor; o `router.replace` para a mesma
      // rota so com outra query nao mudava a URL.
      window.history.replaceState(null, '', escopo.length > 0 ? `/chat?docs=${escopo.join(',')}` : '/chat')
      if (window.matchMedia('(max-width: 767px)').matches) setHistoricoAberto(false)
    },
    [loadThread, acervo]
  )

  const fecharPreview = useCallback(() => setCitacaoAberta(null), [])

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      const scrollContainer = scrollRef.current.querySelector('[data-radix-scroll-area-viewport]')
      if (scrollContainer) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight
      }
    }
  }, [messages, isSending])

  if (loading) {
    return (
      <div className="flex h-dvh flex-col">
        <div className="border-b p-4">
          <Skeleton className="h-8 w-48" />
        </div>
        <div className="flex-1 p-4 space-y-4">
          <Skeleton className="h-16 w-3/4" />
          <Skeleton className="h-16 w-2/3 ml-auto" />
          <Skeleton className="h-16 w-3/4" />
        </div>
      </div>
    )
  }

  if (!user) return null

  const acervoSemDocumentoPronto = acervo !== null && !acervo.some((d) => d.status === 'completed')
  // A resposta em andamento so aparece quando chega o primeiro token; ate la
  // quem ocupa o lugar dela e o "Thinking".
  const visiveis = messages.filter((m) => m.role === 'user' || m.content)
  const aguardandoPrimeiroToken = isSending && messages[messages.length - 1]?.content === ''
  const documentoCitado = citacaoAberta?.document_id
    ? acervo?.find((d) => d.id === citacaoAberta.document_id)
    : undefined

  return (
    <div className="flex h-dvh flex-col">
      <AnimatePresence>
        {citacaoAberta?.document_id && (
          <DocumentPreview
            key={`${citacaoAberta.document_id}-${citacaoAberta.page ?? ''}`}
            id={citacaoAberta.document_id}
            mime={documentoCitado?.mime}
            title={citacaoAberta.document_title}
            page={citacaoAberta.page}
            getToken={getTokenAsync}
            onClose={fecharPreview}
          />
        )}
      </AnimatePresence>

      <div className="flex h-full xl:max-w-[1100px] xl:mx-auto xl:my-4 glass-panel xl:rounded-[2rem] xl:border-gradient xl:ring-1 xl:ring-white/10 xl:shadow-2xl xl:shadow-black/40 overflow-hidden relative">
        <ChatHistory
          open={historicoAberto}
          onClose={() => setHistoricoAberto(false)}
          getToken={getTokenAsync}
          currentThreadId={threadId}
          refreshKey={answerCount}
          onSelectThread={selecionarThread}
          onNewChat={resetChat}
        />
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <ChatHeader
            documentCount={documentIds.length}
            onReset={resetChat}
            hasMessages={messages.length > 0}
            historyOpen={historicoAberto}
            onToggleHistory={() => setHistoricoAberto((v) => !v)}
          />

          <ScrollArea ref={scrollRef} className="flex-1">
            <div className="mx-auto max-w-3xl py-4">
              {messages.length === 0 ? (
                <EmptyState
                  onSuggestionClick={sendMessage}
                  selecionados={documentIds.length}
                  desabilitado={acervoSemDocumentoPronto}
                />
              ) : (
                <AnimatePresence mode="popLayout">
                  {visiveis.map((message) => (
                    <ChatMessage
                      key={message.id}
                      message={message}
                      onCitationClick={setCitacaoAberta}
                    />
                  ))}
                  {aguardandoPrimeiroToken && <ThinkingMessage key="thinking" />}
                  {isSending && workflowSteps.length > 0 && (
                    <motion.div
                      key="workflow"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="px-4 py-2"
                    >
                      <div className="flex flex-col gap-1 pl-11 text-xs text-neutral-400">
                        {workflowSteps.map((step, i) => (
                          <div key={i} className="flex items-center gap-2">
                            {step.status === 'in_progress' ? (
                              <Loader2 className="h-3 w-3 animate-spin text-blue-300" />
                            ) : (
                              <div className="h-3 w-3 rounded-full bg-emerald-400" />
                            )}
                            <span>{step.details}</span>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              )}
            </div>
          </ScrollArea>

          {error && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="mx-auto w-full max-w-3xl px-4 pb-2"
              role="alert"
            >
              <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-2 text-sm text-red-300">
                {error}
                <button
                  onClick={() => setError(null)}
                  className="ml-2 underline hover:no-underline"
                >
                  Dismiss
                </button>
              </div>
            </motion.div>
          )}

          <div className="mx-auto w-full max-w-3xl space-y-2 px-3 pb-2 sm:px-4">
            {/* A trilha existia no backend desde a migracao 003 e nao tinha como ser
                vista: faltava a rota proxy. E o que separa este projeto de um chat
                de provedor, que devolve a resposta e nunca o caminho ate ela. */}
            <DecisionTrail threadId={threadId} answerCount={answerCount} />

            {/* A conversa por voz fica ACIMA do campo de texto, e nao numa pagina
                propria, porque ela nao e outro produto: e outro jeito de perguntar
                ao mesmo acervo. A trilha que ela mostra e a mesma que o chat grava. */}
            <LiveVoice pronto={!acervoSemDocumentoPronto} documentIds={documentIds} />
          </div>

          <ChatInput
            onSend={sendMessage}
            isLoading={isSending}
            onStop={stopGeneration}
            disabled={acervoSemDocumentoPronto}
            asOf={asOf}
            onAsOfChange={setAsOf}
            placeholder={
              acervoSemDocumentoPronto
                ? 'Your library has no ready documents yet. Upload one first.'
                : documentIds.length === 0
                  ? 'Ask anything across your whole library...'
                  : `Ask about the ${documentIds.length} selected ${documentIds.length === 1 ? 'document' : 'documents'}...`
            }
          />
        </div>
      </div>
    </div>
  )
}

function EmptyState({
  onSuggestionClick,
  selecionados,
  desabilitado,
}: {
  onSuggestionClick: (msg: string) => void
  selecionados: number
  desabilitado: boolean
}) {
  const suggestions = [
    { label: 'summarize',  text: 'Summarize the main points of these documents' },
    { label: 'findings',   text: 'What are the key findings?' },
    { label: 'actions',    text: 'Are there any action items mentioned?' },
    { label: 'compare',    text: 'Compare the information across documents' },
  ]

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="px-6 py-16 max-w-2xl mx-auto"
    >
      <div className="flex items-center gap-3 mb-5">
        <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
          00 / Cold start
        </span>
        <span className="h-px flex-1 max-w-[80px] bg-white/10" />
      </div>

      <h2 className="text-3xl sm:text-4xl font-semibold tracking-tighter text-white mb-3">
        Ask anything.
      </h2>
      <p className="text-sm text-neutral-400 max-w-md mb-8 leading-relaxed">
        {selecionados > 0
          ? `Answers come from the ${selecionados} selected ${selecionados === 1 ? 'document' : 'documents'}`
          : 'Answers come from your whole library'}
        , with the passages they were built from. When nothing clears the relevance bar, the
        question is rewritten and searched once more; if that fails too, the best passages still go
        through and the answer is marked low confidence.
      </p>

      <div className="flex flex-col gap-2.5">
        <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-400 mb-1">
          try
        </span>
        {suggestions.map((s, idx) => (
          <button
            key={idx}
            onClick={() => onSuggestionClick(s.text)}
            disabled={desabilitado}
            className="group flex items-center gap-4 text-left rounded-xl bg-white/[0.03] hover:bg-white/[0.06] ring-1 ring-white/10 hover:ring-blue-400/30 transition-all px-4 py-3 disabled:pointer-events-none disabled:opacity-50"
          >
            <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400 shrink-0 w-20">
              {s.label}
            </span>
            <span className="h-3 w-px bg-white/10 shrink-0" />
            <span className="text-sm text-neutral-300 group-hover:text-white transition-colors flex-1">
              {s.text}
            </span>
            <ArrowRight className="h-3.5 w-3.5 text-neutral-600 group-hover:text-blue-300 group-hover:translate-x-0.5 transition-all" />
          </button>
        ))}
      </div>

      <div className="mt-10 flex items-center gap-3 text-[11px] font-mono text-neutral-400">
        <span className="text-blue-300">{'>'}</span>
        <span>waiting for input</span>
        <span className="ml-1 inline-block w-1.5 h-3 bg-blue-300/80 animate-pulse" />
      </div>
    </motion.div>
  )
}

export default function ChatPage() {
  return (
    <Suspense fallback={
      <div className="flex h-dvh items-center justify-center">
        <Skeleton className="h-8 w-32" />
      </div>
    }>
      <ChatPageContent />
    </Suspense>
  )
}
