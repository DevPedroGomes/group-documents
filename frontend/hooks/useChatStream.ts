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
  // Enquanto uma conversa antiga carrega, enviar misturaria a pergunta nova
  // com as mensagens que ainda vao chegar. E a geracao descarta o resultado de
  // uma carga que foi ultrapassada por outra, ou por "New chat".
  const [isLoadingThread, setIsLoadingThread] = useState(false)
  const carregandoThreadRef = useRef(false)
  const geracaoRef = useRef(0)

  const definirThread = useCallback((id: string | null) => {
    threadIdRef.current = id
    setThreadId(id)
  }, [])

  const sendMessage = useCallback(async (content: string) => {
    const texto = content.trim()
    if (!texto || enviandoRef.current || carregandoThreadRef.current) return
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
    let streamAberto = false
    let recebeuDone = false

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
      streamAberto = true

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
              recebeuDone = true
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
      // Sem `done` (evento de erro, queda, parada) o backend grava a trilha do
      // mesmo jeito, no `finally` do stream, antes de fecha-lo: a trilha tem
      // que recarregar tambem.
      if (streamAberto && !recebeuDone) setAnswerCount(n => n + 1)
      setIsLoading(false)
      enviandoRef.current = false
      abortControllerRef.current = null
    }
  }, [definirThread])

  const resetChat = useCallback(() => {
    abortControllerRef.current?.abort()
    geracaoRef.current += 1
    carregandoThreadRef.current = false
    setIsLoadingThread(false)
    setMessages([])
    definirThread(null)
    setIsLoading(false)
    setWorkflowSteps([])
  }, [definirThread])

  const stopGeneration = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  /**
   * Carrega uma conversa antiga. 'cancelado' quando outra carga ou um "New
   * chat" veio depois: o resultado desta e descartado, e nao e erro.
   */
  const loadThread = useCallback(async (
    targetThreadId: string,
  ): Promise<'ok' | 'erro' | 'cancelado'> => {
    abortControllerRef.current?.abort()
    const geracao = ++geracaoRef.current
    carregandoThreadRef.current = true
    setIsLoadingThread(true)
    try {
      const token = await opcoesRef.current.getToken()
      const res = await fetch(`/api/threads/${encodeURIComponent(targetThreadId)}/messages`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (geracao !== geracaoRef.current) return 'cancelado'
      if (!res.ok) return 'erro'

      const data = (await res.json()) as {
        messages?: { role: string; content: string; citations?: Citation[] | null }[]
      }
      if (geracao !== geracaoRef.current) return 'cancelado'
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
      return 'ok'
    } catch (err) {
      if (geracao !== geracaoRef.current) return 'cancelado'
      console.error('Failed to load thread:', err)
      return 'erro'
    } finally {
      if (geracao === geracaoRef.current) {
        carregandoThreadRef.current = false
        setIsLoadingThread(false)
      }
    }
  }, [definirThread])

  return {
    messages,
    isLoading,
    threadId,
    workflowSteps,
    answerCount,
    isLoadingThread,
    sendMessage,
    resetChat,
    stopGeneration,
    loadThread,
  }
}
