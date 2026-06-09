'use client'

import { useEffect, useRef, useState, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { useChatStream } from '@/hooks/useChatStream'
import { ChatHeader } from '@/components/chat/ChatHeader'
import { ChatHistory } from '@/components/chat/ChatHistory'
import { ChatMessage, ThinkingMessage } from '@/components/chat/ChatMessage'
import { ChatInput } from '@/components/chat/ChatInput'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { AnimatePresence, motion } from 'framer-motion'
import { Loader2, ArrowRight } from 'lucide-react'
import type { Citation } from '@/lib/types'

function ChatPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const scrollRef = useRef<HTMLDivElement>(null)
  const { user, loading, getToken } = useAuth()
  const [error, setError] = useState<string | null>(null)

  // Get document IDs from URL
  const documentIds = searchParams.get('docs')?.split(',').filter(Boolean) || []

  // Redirect to home if not authenticated
  useEffect(() => {
    if (!loading && !user) {
      router.push('/')
    }
  }, [loading, user, router])

  const getTokenAsync = async () => getToken() || undefined

  const {
    messages,
    isLoading: isSending,
    sendMessage,
    resetChat,
    stopGeneration,
    workflowSteps,
    threadId,
    loadThread,
  } = useChatStream({
    getToken: getTokenAsync,
    documentIds: documentIds.length > 0 ? documentIds : undefined,
    onError: setError,
  })

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      const scrollContainer = scrollRef.current.querySelector('[data-radix-scroll-area-viewport]')
      if (scrollContainer) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight
      }
    }
  }, [messages, isSending])

  // Handle citation click
  const handleCitationClick = (citation: Citation) => {
    // Could open a preview modal here
    console.log('Citation clicked:', citation)
  }

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

  return (
    <div className="flex h-dvh flex-col">
      <div className="flex h-full xl:max-w-[1100px] xl:mx-auto xl:my-4 glass-panel xl:rounded-[2rem] xl:border-gradient xl:ring-1 xl:ring-white/10 xl:shadow-2xl xl:shadow-black/40 overflow-hidden relative">
        <ChatHistory
          getToken={getTokenAsync}
          currentThreadId={threadId}
          onSelectThread={(id) => loadThread(id, getTokenAsync)}
          onNewChat={resetChat}
        />
        <div className="flex flex-col flex-1 overflow-hidden">
          <ChatHeader
            documentCount={documentIds.length}
            onReset={resetChat}
            hasMessages={messages.length > 0}
          />

          <ScrollArea ref={scrollRef} className="flex-1">
            <div className="mx-auto max-w-3xl py-4">
              {messages.length === 0 ? (
                <EmptyState onSuggestionClick={sendMessage} />
              ) : (
                <AnimatePresence mode="popLayout">
                  {messages.map((message) => (
                    <ChatMessage
                      key={message.id}
                      message={message}
                      onCitationClick={handleCitationClick}
                    />
                  ))}
                  {isSending && <ThinkingMessage key="thinking" />}
                  {isSending && workflowSteps.length > 0 && (
                    <motion.div
                      key="workflow"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="px-4 py-2"
                    >
                      <div className="flex flex-col gap-1 text-xs text-neutral-400">
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
              className="mx-auto max-w-3xl px-4 pb-2"
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

          <ChatInput
            onSend={sendMessage}
            isLoading={isSending}
            onStop={stopGeneration}
            disabled={documentIds.length === 0}
            placeholder={
              documentIds.length === 0
                ? 'No documents selected. Go back and select documents.'
                : 'Ask a question about your documents...'
            }
          />
        </div>
      </div>
    </div>
  )
}

function EmptyState({ onSuggestionClick }: { onSuggestionClick: (msg: string) => void }) {
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
        The pipeline retrieves, grades, rewrites and answers — every claim bound to a source span
        from your selected documents.
      </p>

      <div className="flex flex-col gap-2.5">
        <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500 mb-1">
          try
        </span>
        {suggestions.map((s, idx) => (
          <button
            key={idx}
            onClick={() => onSuggestionClick(s.text)}
            className="group flex items-center gap-4 text-left rounded-xl bg-white/[0.03] hover:bg-white/[0.06] ring-1 ring-white/10 hover:ring-blue-400/30 transition-all px-4 py-3"
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

      <div className="mt-10 flex items-center gap-3 text-[11px] font-mono text-neutral-500">
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
