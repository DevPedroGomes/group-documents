'use client'

import { useState, useCallback, useRef, useEffect } from 'react'

export interface Citation {
  document_id: string
  document_title: string
  page: number
  snippet: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  timestamp: Date
}

interface UseChatOptions {
  getToken: () => Promise<string | undefined>
  documentIds?: string[]
  onError?: (error: string) => void
}

export function useChat({ getToken, documentIds, onError }: UseChatOptions) {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [threadId, setThreadId] = useState<string | null>(null)
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

      const data = await res.json()

      if (data.thread_id && !threadId) {
        setThreadId(data.thread_id)
      }

      const assistantMessage: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: data.answer,
        citations: data.citations,
        timestamp: new Date(),
      }

      setMessages(prev => [...prev, assistantMessage])
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        return
      }
      const errorMessage = error instanceof Error ? error.message : 'Something went wrong'
      onError?.(errorMessage)
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
    sendMessage,
    resetChat,
    stopGeneration,
  }
}
