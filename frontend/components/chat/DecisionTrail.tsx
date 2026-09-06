'use client'

/**
 * A trilha de decisao: por que o agente respondeu aquilo.
 *
 * POR QUE ESTE COMPONENTE EXISTE
 * A tabela `decisions` guarda o caminho de cada resposta desde a migracao 003:
 * quais variantes de query foram geradas, quais trechos entraram e com que
 * score, o que o grader descartou, se o rerank rodou, se caiu na rede de baixa
 * confianca, se duas fontes divergiram, e quanto demorou. Tudo isso existia,
 * estava indexado, e NAO TINHA COMO SER VISTO: o frontend nao tinha rota proxy
 * para /decisions. Feature construida, testada e desligada.
 *
 * E o que ela mostra e exatamente o que um chat de provedor nao mostra por
 * projeto: eles devolvem a resposta, nunca o caminho ate ela.
 */

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CalendarClock, ChevronDown, ChevronRight, Route } from 'lucide-react'

import { cn } from '@/lib/utils'
import { fetchWithAuth } from '@/lib/auth'

type Decisao = {
  id: string
  question: string
  considered: number
  kept: number
  score_scale: string
  score_scale_hint: string
  reranked: boolean
  low_confidence: boolean
  web_used: boolean
  answered: boolean
  conflict: { summary: string; sources: string[] } | null
  as_of: string | null
  latency_ms: number | null
  created_at: string | null
}

export function DecisionTrail({
  threadId,
  className,
}: {
  threadId: string | null
  className?: string
}) {
  const [aberto, setAberto] = useState(false)
  const [decisoes, setDecisoes] = useState<Decisao[]>([])
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)

  const carregar = useCallback(async () => {
    if (!threadId) return
    setCarregando(true)
    setErro(null)
    try {
      const r = await fetchWithAuth(
        `/api/decisions?thread_id=${encodeURIComponent(threadId)}&limit=50`
      )
      if (!r.ok) throw new Error('Could not load the decision trail')
      const d = (await r.json()) as { decisions: Decisao[] }
      setDecisoes(d.decisions ?? [])
    } catch (e) {
      setErro(e instanceof Error ? e.message : 'Could not load the decision trail')
    } finally {
      setCarregando(false)
    }
  }, [threadId])

  // Recarrega ao abrir e sempre que a thread muda. Nao faz polling: a trilha e
  // gravada DEPOIS da resposta, entao abrir o painel ja e o momento certo.
  useEffect(() => {
    if (aberto) void carregar()
  }, [aberto, carregar])

  if (!threadId) return null

  return (
    <div className={cn('rounded-lg border bg-card', className)}>
      <button
        onClick={() => setAberto((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-3 text-sm font-medium hover:bg-muted/50"
      >
        {aberto ? (
          <ChevronDown className="h-4 w-4 shrink-0 opacity-60" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 opacity-60" />
        )}
        <Route className="h-4 w-4 shrink-0 opacity-60" />
        Why these answers?
        {decisoes.length > 0 && (
          <span className="ml-auto text-xs font-normal text-muted-foreground">
            {decisoes.length} {decisoes.length === 1 ? 'step' : 'steps'}
          </span>
        )}
      </button>

      {aberto && (
        <div className="border-t px-4 py-3">
          {carregando && <p className="text-sm text-muted-foreground">Loading…</p>}
          {erro && <p className="text-sm text-destructive">{erro}</p>}
          {!carregando && !erro && decisoes.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing yet. Ask something and the trail fills in.
            </p>
          )}
          <div className="space-y-3">
            {decisoes.map((d) => (
              <UmaDecisao key={d.id} d={d} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function UmaDecisao({ d }: { d: Decisao }) {
  return (
    <div className="rounded-md border border-dashed bg-muted/30 p-3 text-sm">
      <p className="font-medium">“{d.question}”</p>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>
          {d.considered} retrieved → <span className="font-medium">{d.kept} kept</span>
        </span>
        {d.reranked && <Selo>reranked</Selo>}
        {d.web_used && <Selo>web</Selo>}
        {d.latency_ms != null && <span>{d.latency_ms} ms</span>}
        {!d.answered && <Selo tom="alerta">no answer</Selo>}
      </div>

      {/* A escala vem do backend porque o numero sozinho engana: 0,03 e otimo em
          RRF e pessimo em Cohere. Foi o bug que fez toda resposta sair com aviso
          de baixa confianca. */}
      <p className="mt-1 text-[11px] text-muted-foreground/80">{d.score_scale_hint}</p>

      {d.as_of && (
        <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
          <CalendarClock className="h-3.5 w-3.5 shrink-0" />
          answered as of {d.as_of.slice(0, 10)}
        </p>
      )}

      {d.low_confidence && !d.conflict && (
        <p className="mt-2 text-xs text-muted-foreground">
          Low confidence: nothing retrieved cleared the bar, so the agent should say so instead
          of guessing.
        </p>
      )}

      {d.conflict && (
        <div className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          <p className="flex items-center gap-2 font-medium text-amber-700 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            Your documents disagree
          </p>
          <p className="mt-1 text-muted-foreground">{d.conflict.summary}</p>
          {d.conflict.sources?.length > 0 && (
            <ul className="mt-1 list-inside list-disc text-muted-foreground">
              {d.conflict.sources.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function Selo({ children, tom }: { children: React.ReactNode; tom?: 'alerta' }) {
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide',
        tom === 'alerta'
          ? 'bg-amber-500/15 text-amber-700 dark:text-amber-400'
          : 'bg-muted text-muted-foreground'
      )}
    >
      {children}
    </span>
  )
}
