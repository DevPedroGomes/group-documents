'use client'

import { useEffect, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { MessageSquare, Plus, ChevronLeft } from 'lucide-react'
import type { ThreadSummary } from '@/lib/types'

interface ChatHistoryProps {
  getToken: () => Promise<string | undefined>
  currentThreadId: string | null
  onSelectThread: (threadId: string) => void
  onNewChat: () => void
}

export function ChatHistory({
  getToken,
  currentThreadId,
  onSelectThread,
  onNewChat,
}: ChatHistoryProps) {
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [isOpen, setIsOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(false)

  const fetchThreads = useCallback(async () => {
    setIsLoading(true)
    try {
      const token = await getToken()
      const res = await fetch('/api/threads', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setThreads(data.threads || [])
      }
    } catch (err) {
      console.error('Failed to fetch threads:', err)
    } finally {
      setIsLoading(false)
    }
  }, [getToken])

  useEffect(() => {
    if (isOpen) {
      fetchThreads()
    }
  }, [isOpen, fetchThreads])

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
    <>
      {/* Toggle button */}
      <Button
        variant="ghost"
        size="icon"
        className="absolute top-3 left-3 z-10 rounded-full h-8 w-8"
        onClick={() => setIsOpen(!isOpen)}
      >
        {isOpen ? (
          <ChevronLeft className="h-4 w-4" />
        ) : (
          <MessageSquare className="h-4 w-4" />
        )}
      </Button>

      {/* Sidebar */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 280, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="border-r border-zinc-200 bg-white/80 backdrop-blur-sm overflow-hidden shrink-0"
          >
            <div className="flex flex-col h-full w-[280px]">
              <div className="p-3 border-b border-zinc-100">
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full gap-2 text-xs"
                  onClick={onNewChat}
                >
                  <Plus className="h-3 w-3" />
                  New Chat
                </Button>
              </div>

              <ScrollArea className="flex-1">
                <div className="p-2">
                  {isLoading ? (
                    <p className="text-xs text-zinc-400 text-center py-4">Loading...</p>
                  ) : threads.length === 0 ? (
                    <p className="text-xs text-zinc-400 text-center py-4">No conversations yet</p>
                  ) : (
                    Object.entries(grouped).map(([label, items]) => (
                      <div key={label} className="mb-3">
                        <p className="text-[10px] font-semibold text-zinc-400 uppercase tracking-widest px-2 mb-1">
                          {label}
                        </p>
                        {items.map((thread) => (
                          <button
                            key={thread.id}
                            onClick={() => onSelectThread(thread.id)}
                            className={`w-full text-left px-3 py-2 rounded-lg text-sm truncate transition-colors ${
                              currentThreadId === thread.id
                                ? 'bg-orange-50 text-orange-700 font-medium'
                                : 'text-zinc-600 hover:bg-zinc-100'
                            }`}
                          >
                            {thread.title || 'Untitled'}
                          </button>
                        ))}
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
