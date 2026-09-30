'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import type { Message, Citation, WorkflowStep, SSEEvent, DoneEvent } from '@/lib/types'

interface UseChatStreamOptions {
  getToken: () => Promise<string | undefined>
  /** Vazio ou ausente: a pergunta vai para o acervo inteiro. */
  documentIds?: string[]
  /** Recorte no tempo, "YYYY-MM-DD". Vazio responde com o acervo de hoje. */
  asOf?: string | null
  onError?: (error: string) => void
  /** Cada `done`, com o escopo que a pergunta usou. */
  onDone?: (done: DoneEvent, documentIds: string[] | undefined) => void
}

/** O `detail` do FastAPI e string no HTTPException e lista no 422 de validacao. */
function mensagemDeErro(corpo: unknown, status: number): string {
  const detail = (corpo as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (status === 422) return 'That request was not valid. Check the date and try again.'
  if (status === 429) return 'Too many questions right now. Try again in a moment.'
  return 'Failed to send message'
}

function lerEvento(json: string): SSEEvent | null {
  try {
    return JSON.parse(json) as SSEEvent
  } catch (parseError) {
    console.warn('Malformed SSE event:', json, parseError)
    return null
  }
}

export function useChatStream(options: UseChatStreamOptions) {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [workflowSteps, setWorkflowSteps] = useState<WorkflowStep[]>([])
  /** Quantos `done` chegaram: a trilha e o historico recarregam quando muda. */
  const [answerCount, setAnswerCount] = useState(0)
  const abortControllerRef = useRef<AbortController | null>(null)

  // As opcoes vivem num ref para `sendMessage` e `loadThread` serem estaveis:
  // recria-los a cada render (ou a cada token) disparava os efeitos de quem os
  // recebe, e o historico era buscado de novo a cada token.
  const opcoesRef = useRef(options)
  useEffect(() => {
    opcoesRef.current = options
  })
  const threadIdRef = useRef<string | null>(null)
  const enviandoRef = useRef(false)

  const definirThread = useCallback((id: string | null) => {
    threadIdRef.current = id
    setThreadId(id)
  }, [])

  const sendMessage = useCallback(async (content: string) => {
    const texto = content.trim()
    if (!texto || enviandoRef.current) return
    enviandoRef.current = true

    const { getToken, documentIds, asOf, onError, onDone } = opcoesRef.current
    const escopo = documentIds && documentIds.length > 0 ? documentIds : undefined
    const assistantId = crypto.randomUUID()
    const atualizar = (patch: (m: Message) => Message) =>
      setMessages(prev => prev.map(m => (m.id === assistantId ? patch(m) : m)))

    setMessages(prev => [
      ...prev,
      { id: crypto.randomUUID(), role: 'user', content: texto, timestamp: new Date() },
      { id: assistantId, role: 'assistant', content: '', timestamp: new Date() },
    ])
    setIsLoading(true)
    setWorkflowSteps([])

    try {
      const token = await getToken()
      const controller = new AbortController()
      abortControllerRef.current = controller

      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: texto,
          document_ids: escopo,
          thread_id: threadIdRef.current ?? undefined,
          as_of: asOf || undefined,
        }),
        signal: controller.signal,
      })

      if (!res.ok) {
        const data: unknown = await res.json().catch(() => null)
        throw new Error(mensagemDeErro(data, res.status))
      }

      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''

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

          const event = lerEvento(jsonStr)
          if (!event) continue

          switch (event.type) {
            case 'workflow':
              setWorkflowSteps(event.data)
              break

            case 'sources': {
              // As fontes chegam antes do primeiro token: aparecem ja durante a geracao.
              const citacoes: Citation[] = Array.isArray(event.data) ? event.data : []
              atualizar(m => ({ ...m, citations: citacoes }))
              break
            }

            case 'conflict':
              // Pode vir antes, entre ou depois dos tokens: fica na mensagem,
              // e nao no painel de passos, que some quando o stream acaba.
              atualizar(m => ({ ...m, conflict: event.data }))
              break

            case 'chunk':
              atualizar(m => ({ ...m, content: m.content + event.data }))
              break

            case 'done':
              if (event.data.thread_id) definirThread(event.data.thread_id)
              atualizar(m => ({
                ...m,
                messageId: event.data.message_id,
                lowConfidence: Boolean(event.data.low_confidence),
              }))
              setAnswerCount(n => n + 1)
              onDone?.(event.data, escopo)
              break

            case 'error':
              onError?.(event.data?.message || 'An error occurred')
              break
          }
        }
      }
    } catch (error) {
      if (!(error instanceof Error && error.name === 'AbortError')) {
        onError?.(error instanceof Error ? error.message : 'Something went wrong')
      }
    } finally {
      // Resposta sem nenhum texto (erro, parada) nao fica como balao vazio.
      setMessages(prev => prev.filter(m => m.id !== assistantId || m.content))
      setIsLoading(false)
      enviandoRef.current = false
      abortControllerRef.current = null
    }
  }, [definirThread])

  const resetChat = useCallback(() => {
    abortControllerRef.current?.abort()
    setMessages([])
    definirThread(null)
    setIsLoading(false)
    setWorkflowSteps([])
  }, [definirThread])

  const stopGeneration = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  /** Carrega uma conversa antiga. Devolve false quando nao conseguiu. */
  const loadThread = useCallback(async (targetThreadId: string): Promise<boolean> => {
    abortControllerRef.current?.abort()
    try {
      const token = await opcoesRef.current.getToken()
      const res = await fetch(`/api/threads/${encodeURIComponent(targetThreadId)}/messages`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) return false

      const data = (await res.json()) as {
        messages?: { role: string; content: string; citations?: Citation[] | null }[]
      }
      const loadedMessages: Message[] = (data.messages || []).map((m, i) => ({
        id: `loaded-${targetThreadId}-${i}`,
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.content,
        citations: m.citations ?? undefined,
        timestamp: new Date(),
      }))

      setMessages(loadedMessages)
      definirThread(targetThreadId)
      setWorkflowSteps([])
      return true
    } catch (err) {
      console.error('Failed to load thread:', err)
      return false
    }
  }, [definirThread])

  return {
    messages,
    isLoading,
    threadId,
    workflowSteps,
    answerCount,
    sendMessage,
    resetChat,
    stopGeneration,
    loadThread,
  }
}
