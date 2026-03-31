'use client'

import { useEffect, useRef, useState, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'
import { useChat } from '@/hooks/useChat'
import { ChatHeader } from '@/components/chat/ChatHeader'
import { ChatMessage, ThinkingMessage } from '@/components/chat/ChatMessage'
import { ChatInput } from '@/components/chat/ChatInput'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { AnimatePresence, motion } from 'framer-motion'
import { MessageSquare, Sparkles } from 'lucide-react'
import type { Citation } from '@/hooks/useChat'

const supabase = createClient()

function ChatPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [session, setSession] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Get document IDs from URL
  const documentIds = searchParams.get('docs')?.split(',').filter(Boolean) || []

  // Auth check
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) {
        router.push('/')
      } else {
        setSession(data.session)
      }
      setLoading(false)
    })

    const { data: listener } = supabase.auth.onAuthStateChange((_e, s) => {
      if (!s) router.push('/')
      setSession(s)
    })

    return () => listener.subscription.unsubscribe()
  }, [router])

  const {
    messages,
    isLoading: isSending,
    sendMessage,
    resetChat,
    stopGeneration,
  } = useChat({
    getToken: async () => (await supabase.auth.getSession()).data.session?.access_token,
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

  if (!session) return null

  return (
    <div className="flex h-dvh flex-col bg-background">
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
          <div className="rounded-lg bg-destructive/10 px-4 py-2 text-sm text-destructive">
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
  )
}

function EmptyState({ onSuggestionClick }: { onSuggestionClick: (msg: string) => void }) {
  const suggestions = [
    'Summarize the main points of these documents',
    'What are the key findings?',
    'Are there any action items mentioned?',
    'Compare the information across documents',
  ]

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex flex-col items-center justify-center px-4 py-16"
    >
      <div className="mb-6 rounded-full bg-primary/10 p-4">
        <Sparkles className="h-8 w-8 text-primary" />
      </div>

      <h2 className="mb-2 text-xl font-semibold">Start a Conversation</h2>
      <p className="mb-8 text-center text-muted-foreground max-w-md">
        Ask questions about your selected documents. The AI will search through them and provide answers with citations.
      </p>

      <div className="grid gap-2 w-full max-w-md">
        {suggestions.map((suggestion, idx) => (
          <Button
            key={idx}
            variant="outline"
            className="justify-start text-left h-auto py-3 px-4"
            onClick={() => onSuggestionClick(suggestion)}
          >
            <MessageSquare className="h-4 w-4 mr-2 shrink-0 text-muted-foreground" />
            <span className="text-sm">{suggestion}</span>
          </Button>
        ))}
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
