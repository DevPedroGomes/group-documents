'use client'

import { useState, useCallback, useRef } from 'react'
import type { Message, Citation, WorkflowStep, SSEEvent } from '@/lib/types'

interface UseChatStreamOptions {
  getToken: () => Promise<string | undefined>
  documentIds?: string[]
  onError?: (error: string) => void
}

export function useChatStream({ getToken, documentIds, onError }: UseChatStreamOptions) {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [workflowSteps, setWorkflowSteps] = useState<WorkflowStep[]>([])
  const [currentCitations, setCurrentCitations] = useState<Citation[]>([])
  const abortControllerRef = useRef<AbortController | null>(null)

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || isLoading) return

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: content.trim(),
      timestamp: new Date(),
    }

    setMessages(prev => [...prev, userMessage])
    setIsLoading(true)
    setWorkflowSteps([])
    setCurrentCitations([])

    // Add placeholder assistant message for streaming
    const assistantId = crypto.randomUUID()
    setMessages(prev => [...prev, {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    }])

    try {
      const token = await getToken()
      abortControllerRef.current = new AbortController()

      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: content.trim(),
          document_ids: documentIds,
          thread_id: threadId,
        }),
        signal: abortControllerRef.current.signal,
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to send message')
      }

      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''
      let finalCitations: Citation[] = []

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const jsonStr = line.slice(6)
          if (!jsonStr.trim()) continue

          try {
            const event: SSEEvent = JSON.parse(jsonStr)

            switch (event.type) {
              case 'workflow':
                setWorkflowSteps(event.data)
                break

              case 'sources':
                finalCitations = event.data
                setCurrentCitations(event.data)
                break

              case 'chunk':
                setMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? { ...m, content: m.content + event.data }
                      : m
                  )
                )
                break

              case 'done':
                if (event.data.thread_id && !threadId) {
                  setThreadId(event.data.thread_id)
                }
                // Attach citations to final message
                setMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? { ...m, citations: finalCitations }
                      : m
                  )
                )
                break

              case 'error':
                onError?.(event.data.message || 'An error occurred')
                break
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        return
      }
      const errorMessage = error instanceof Error ? error.message : 'Something went wrong'
      onError?.(errorMessage)
      // Remove empty assistant message on error
      setMessages(prev => prev.filter(m => m.id !== assistantId || m.content))
    } finally {
      setIsLoading(false)
      abortControllerRef.current = null
    }
  }, [getToken, documentIds, threadId, isLoading, onError])

  const resetChat = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    setMessages([])
    setThreadId(null)
    setIsLoading(false)
    setWorkflowSteps([])
    setCurrentCitations([])
  }, [])

  const stopGeneration = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      setIsLoading(false)
    }
  }, [])

  return {
    messages,
    isLoading,
    threadId,
    workflowSteps,
    currentCitations,
    sendMessage,
    resetChat,
    stopGeneration,
  }
}
