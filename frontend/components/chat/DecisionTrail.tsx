'use client'

/**
 * A trilha de decisao: por que o agente respondeu aquilo.
 *
 * POR QUE ESTE COMPONENTE EXISTE
 * A tabela `decisions` guarda o caminho de cada resposta desde a migracao 003:
 * quais consultas foram buscadas, quais trechos entraram e com que score, o
 * que sobrou depois do grader, se o rerank rodou, se caiu na rede de baixa
 * confianca, se duas fontes divergiram, e quanto demorou. Tudo isso existia,
 * estava indexado, e NAO TINHA COMO SER VISTO: o frontend nao tinha rota proxy
 * para /decisions. Feature construida, testada e desligada.
 *
 * E o que ela mostra e exatamente o que um chat de provedor nao mostra por
 * projeto: eles devolvem a resposta, nunca o caminho ate ela.
 */

import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  Globe,
  Route,
  Search,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import { fetchWithAuth } from '@/lib/auth'
import type { Decision, DecisionPassage } from '@/lib/types'

export function DecisionTrail({
  threadId,
  answerCount,
  className,
}: {
  threadId: string | null
  /** Muda a cada resposta concluida; a trilha dela ja foi gravada antes do `done`. */
  answerCount: number
  className?: string
}) {
  const [aberto, setAberto] = useState(false)
  const [decisoes, setDecisoes] = useState<Decision[]>([])
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const carregouRef = useRef(false)

  // Outra thread: a lista antiga nao pode aparecer como se fosse desta.
  useEffect(() => {
    setDecisoes([])
    carregouRef.current = false
  }, [threadId])

  // Carrega ao abrir, ao trocar de thread e a cada resposta nova. Antes so
  // carregava ao abrir: com o painel aberto, a trilha da pergunta que acabou
  // de ser respondida nao aparecia. Fechado e nunca aberto, nao busca nada.
  useEffect(() => {
    if (!threadId || (!aberto && !carregouRef.current)) return
    let cancelado = false
    setCarregando(true)
    setErro(null)
    ;(async () => {
      try {
        const r = await fetchWithAuth(
          `/api/decisions?thread_id=${encodeURIComponent(threadId)}&limit=50`
        )
        if (!r.ok) throw new Error('Could not load the decision trail')
        const d = (await r.json()) as { decisions?: Decision[] }
        if (cancelado) return
        setDecisoes(d.decisions ?? [])
        carregouRef.current = true
      } catch (e) {
        if (!cancelado) setErro(e instanceof Error ? e.message : 'Could not load the decision trail')
      } finally {
        if (!cancelado) setCarregando(false)
      }
    })()
    return () => {
      cancelado = true
    }
  }, [aberto, threadId, answerCount])

  if (!threadId) return null

  return (
    <div className={cn('rounded-xl bg-white/[0.03] ring-1 ring-white/10', className)}>
      <button
        onClick={() => setAberto((v) => !v)}
        aria-expanded={aberto}
        className="flex w-full items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-neutral-200 hover:bg-white/[0.04]"
      >
        {aberto ? (
          <ChevronDown className="h-4 w-4 shrink-0 opacity-60" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 opacity-60" />
        )}
        <Route className="h-4 w-4 shrink-0 text-blue-300" />
        Why these answers?
        {decisoes.length > 0 && (
          <span className="ml-auto text-xs font-normal text-neutral-400">
            {decisoes.length} {decisoes.length === 1 ? 'answer' : 'answers'}
          </span>
        )}
      </button>

      {aberto && (
        // Teto de altura: aberta, a trilha nao pode empurrar o campo de texto
        // para fora da tela no celular.
        <div className="max-h-[32vh] overflow-y-auto border-t border-white/10 px-3 py-3 sm:px-4">
          {carregando && decisoes.length === 0 && <p className="text-sm text-neutral-400">Loading…</p>}
          {erro && <p className="text-sm text-red-300">{erro}</p>}
          {!carregando && !erro && decisoes.length === 0 && (
            <p className="text-sm text-neutral-400">
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

/** O score so faz sentido com a escala: 0,03 e otimo em RRF e pessimo na Cohere. */
function formatarScore(score: number, escala: string | undefined): string {
  if (!Number.isFinite(score)) return '–'
  return escala === 'rrf' ? score.toFixed(4) : score.toFixed(2)
}

/** Mesmo trecho em `retrieved` e `graded`: o backend grava os dois do mesmo lote. */
function chaveDoTrecho(p: DecisionPassage): string {
  return `${p.document_id ?? p.url ?? p.document_title ?? ''}|${p.page ?? ''}|${p.score}`
}

/** Todos os trechos recuperados, marcando os que foram para a resposta. */
function trechosDaDecisao(d: Decision): { p: DecisionPassage; mantido: boolean }[] {
  const recuperados = Array.isArray(d.retrieved) ? d.retrieved : []
  const mantidos = Array.isArray(d.graded) ? d.graded : []
  const chavesMantidas = new Set(mantidos.map(chaveDoTrecho))
  const chavesRecuperadas = new Set(recuperados.map(chaveDoTrecho))
  return [
    ...recuperados.map((p) => ({ p, mantido: chavesMantidas.has(chaveDoTrecho(p)) })),
    // Resultado web nao passa pelo retrieve: entra direto no que foi ao gerador.
    ...mantidos.filter((p) => !chavesRecuperadas.has(chaveDoTrecho(p))).map((p) => ({ p, mantido: true })),
  ]
}

function UmaDecisao({ d }: { d: Decision }) {
  const consultas = Array.isArray(d.queries) ? d.queries : []
  const trechos = trechosDaDecisao(d)
  const fontesDoConflito = Array.isArray(d.conflict?.sources) ? d.conflict.sources : []

  return (
    <div className="rounded-lg border border-dashed border-white/10 bg-white/[0.02] p-3 text-sm">
      <p className="font-medium text-white">“{d.question}”</p>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-neutral-400">
        <span>
          {d.considered} retrieved → <span className="font-medium text-neutral-200">{d.kept} kept</span>
        </span>
        {d.reranked && <Selo>reranked</Selo>}
        {d.web_used && <Selo>web</Selo>}
        {d.low_confidence && <Selo tom="alerta">low confidence</Selo>}
        {d.latency_ms != null && <span>{(d.latency_ms / 1000).toFixed(1)} s</span>}
        {!d.answered && <Selo tom="alerta">no answer</Selo>}
      </div>

      {d.as_of && (
        <p className="mt-2 flex items-center gap-2 text-xs text-blue-200">
          <CalendarClock className="h-3.5 w-3.5 shrink-0" />
          answered as of {d.as_of.slice(0, 10)}
        </p>
      )}

      {consultas.length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-[10px] font-mono uppercase tracking-widest text-neutral-400">
            Searched for
          </p>
          <ol className="space-y-0.5">
            {consultas.map((q, i) => (
              <li key={`${i}-${q}`} className="flex gap-2 text-xs text-neutral-300">
                <Search className="mt-0.5 h-3 w-3 shrink-0 text-neutral-500" aria-hidden />
                <span className="min-w-0 break-words">{q}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {trechos.length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-[10px] font-mono uppercase tracking-widest text-neutral-400">
            Passages
          </p>
          <ul className="divide-y divide-white/5 rounded-md ring-1 ring-white/5">
            {trechos.map(({ p, mantido }, i) => (
              <Trecho key={i} p={p} mantido={mantido} />
            ))}
          </ul>
          {/* A escala vem do backend porque o numero sozinho engana. Foi o bug
              que fez toda resposta sair com aviso de baixa confianca. */}
          <p className="mt-1.5 text-[11px] text-neutral-400">
            <span className="font-mono uppercase">{d.score_scale}</span> · {d.score_scale_hint}
          </p>
        </div>
      )}

      {d.low_confidence && !d.conflict && (
        <p className="mt-2 text-xs text-neutral-400">
          Low confidence: the search found little or nothing clearly relevant, so the answer may
          be incomplete.
        </p>
      )}

      {d.conflict && (
        <div className="mt-3 rounded-md border border-amber-400/30 bg-amber-400/[0.07] p-2.5 text-xs">
          <p className="flex items-center gap-2 font-semibold text-amber-200">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            Your documents disagree
          </p>
          <p className="mt-1 text-neutral-300">{d.conflict.summary}</p>
          {fontesDoConflito.length > 0 && (
            <ul className="mt-1 list-inside list-disc text-neutral-400">
              {fontesDoConflito.map((s) => (
                <li key={s}>
                  {s}
                  {s === d.conflict?.vigente && (
                    <span className="ml-1.5 text-emerald-200">· most recent document date</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function Trecho({ p, mantido }: { p: DecisionPassage; mantido: boolean }) {
  const web = !p.document_id && Boolean(p.url)
  const titulo = p.document_title || (web ? 'Web result' : 'Untitled')
  return (
    <li className={cn('flex items-start gap-2 px-2.5 py-1.5 text-xs', !mantido && 'opacity-75')}>
      {web ? (
        <Globe className="mt-0.5 h-3.5 w-3.5 shrink-0 text-violet-300" aria-hidden />
      ) : (
        <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-300" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-neutral-200">
          {titulo}
          {p.page != null && <span className="ml-1.5 text-neutral-400">p.{p.page}</span>}
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] font-mono text-neutral-400">
          <span className={mantido ? 'text-emerald-300' : 'text-neutral-400'}>
            {mantido ? 'kept' : 'dropped'}
          </span>
          {web && <span className="text-violet-300">web</span>}
          {p.document_date && <span>dated {p.document_date}</span>}
          {web && p.url && /^https?:\/\//i.test(p.url) && (
            <a
              href={p.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-0.5 hover:text-white"
            >
              open <ExternalLink className="h-2.5 w-2.5" aria-hidden />
            </a>
          )}
        </p>
      </div>
      <span className="shrink-0 font-mono text-[11px] text-neutral-300" title={`${p.score_scale ?? ''} score`}>
        {formatarScore(p.score, p.score_scale)}
        {p.score_scale && p.score_scale !== 'cohere' && (
          <span className="ml-1 text-[9px] uppercase text-neutral-400">{p.score_scale}</span>
        )}
      </span>
    </li>
  )
}

function Selo({ children, tom }: { children: React.ReactNode; tom?: 'alerta' }) {
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide',
        tom === 'alerta' ? 'bg-amber-400/15 text-amber-200' : 'bg-white/5 text-neutral-300'
      )}
    >
      {children}
    </span>
  )
}
