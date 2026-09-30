'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Plus, X } from 'lucide-react'
import type { ThreadSummary } from '@/lib/types'

interface ChatHistoryProps {
  open: boolean
  onClose: () => void
  getToken: () => Promise<string | undefined>
  currentThreadId: string | null
  /** Muda a cada resposta concluida: uma thread nova precisa aparecer na lista. */
  refreshKey: number
  onSelectThread: (threadId: string) => void
  onNewChat: () => void
}

export function ChatHistory({
  open,
  onClose,
  getToken,
  currentThreadId,
  refreshKey,
  onSelectThread,
  onNewChat,
}: ChatHistoryProps) {
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [isLoading, setIsLoading] = useState(false)

  // `getToken` num ref: se quem chama recriar a funcao a cada render, a lista
  // nao pode ser buscada de novo por isso. Antes era buscada a cada token.
  const getTokenRef = useRef(getToken)
  useEffect(() => {
    getTokenRef.current = getToken
  })

  // Busca so com o painel aberto: ao abrir, ao trocar de thread e depois de
  // cada resposta. Fechado, a proxima abertura ja busca a lista atual.
  useEffect(() => {
    if (!open) return
    let cancelado = false
    setIsLoading(true)
    ;(async () => {
      try {
        const token = await getTokenRef.current()
        const res = await fetch('/api/threads', {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (res.ok && !cancelado) {
          const data = (await res.json()) as { threads?: ThreadSummary[] }
          setThreads(data.threads || [])
        }
      } catch (err) {
        console.error('Failed to fetch threads:', err)
      } finally {
        if (!cancelado) setIsLoading(false)
      }
    })()
    return () => {
      cancelado = true
    }
  }, [open, currentThreadId, refreshKey])

  // Group threads by date
  const today = new Date().toDateString()
  const yesterday = new Date(Date.now() - 86400000).toDateString()

  const grouped = threads.reduce<Record<string, ThreadSummary[]>>((acc, thread) => {
    const date = new Date(thread.updated_at).toDateString()
    let label = date
    if (date === today) label = 'Today'
    else if (date === yesterday) label = 'Yesterday'

    if (!acc[label]) acc[label] = []
    acc[label].push(thread)
    return acc
  }, {})

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: 280, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ duration: 0.2 }}
          aria-label="Conversation history"
          // No celular a lista sobrepoe o chat em vez de espremer a coluna da
          // conversa para uns 90px.
          className="absolute inset-y-0 left-0 z-30 border-r border-white/10 bg-neutral-950/95 shadow-2xl shadow-black/60 backdrop-blur overflow-hidden shrink-0 md:static md:z-auto md:bg-white/[0.03] md:shadow-none"
        >
          <div className="flex flex-col h-full w-[280px]">
            <div className="flex items-center gap-2 p-3 border-b border-white/10">
              <Button
                variant="outline"
                size="sm"
                className="flex-1 gap-2 text-xs"
                onClick={onNewChat}
              >
                <Plus className="h-3 w-3" />
                New Chat
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-9 w-9 shrink-0 rounded-full"
                onClick={onClose}
                aria-label="Close history"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>

            <ScrollArea className="flex-1">
              <div className="p-2">
                {isLoading && threads.length === 0 ? (
                  <p className="text-xs font-mono text-neutral-400 text-center py-4">loading…</p>
                ) : threads.length === 0 ? (
                  <p className="text-xs font-mono text-neutral-400 text-center py-4">no threads yet</p>
                ) : (
                  Object.entries(grouped).map(([label, items], gIdx) => (
                    <div key={label} className="mb-4">
                      <div className="flex items-center gap-2 px-2 mb-1.5">
                        <span className="text-[9px] font-mono uppercase tracking-widest text-blue-400">
                          {`0${gIdx + 1}`}
                        </span>
                        <span className="text-[10px] font-semibold text-neutral-400 uppercase tracking-widest">
                          {label}
                        </span>
                        <span className="h-px flex-1 bg-white/5" />
                      </div>
                      {items.map((thread) => {
                        const active = currentThreadId === thread.id
                        return (
                          <button
                            key={thread.id}
                            onClick={() => onSelectThread(thread.id)}
                            aria-current={active ? 'true' : undefined}
                            className={`group relative w-full text-left pl-3.5 pr-3 py-2 rounded-lg text-sm truncate transition-colors ${
                              active
                                ? 'bg-blue-400/10 text-blue-100'
                                : 'text-neutral-300 hover:bg-white/[0.04]'
                            }`}
                          >
                            {active && (
                              <span className="absolute left-0 top-2 bottom-2 w-0.5 rounded-r bg-blue-400" />
                            )}
                            <span className="block truncate">
                              {thread.title || 'Untitled'}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  ))
                )}
              </div>
            </ScrollArea>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}
