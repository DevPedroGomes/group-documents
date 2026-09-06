'use client'

/**
 * Conversa por voz sobre o acervo, com a trilha aparecendo enquanto se fala.
 *
 * A UI existe para deixar VISIVEL o que separa isto de "voz colada num RAG", e
 * o que um chat de provedor nao mostra por projeto: cada `buscar_no_acervo` que
 * o modelo dispara entra na linha do tempo com a pergunta que ele mesmo formulou,
 * quantos trechos sobreviveram ao grader, e o aviso quando duas fontes divergem.
 *
 * Sem isso o visitante ouve uma resposta bonita e nao tem como saber se ela veio
 * dos documentos dele ou da memoria do modelo, que e exatamente a duvida que
 * este projeto existe para responder.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CalendarClock, Mic, Search, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  RealtimeSession,
  type BuscaNaConversa,
  type EstadoRealtime,
} from '@/lib/realtime-session'

type Item =
  | { tipo: 'fala'; id: string; quem: 'pessoa' | 'agente'; texto: string }
  | { tipo: 'busca'; id: string; busca: BuscaNaConversa }

const ROTULO: Record<EstadoRealtime, string> = {
  parado: 'Start a conversation',
  conectando: 'Connecting…',
  ouvindo: 'Listening — just talk',
  buscando: 'Searching your archive…',
  falando: 'Speaking',
}

export function LiveVoice({ pronto }: { pronto: boolean }) {
  const [estado, setEstado] = useState<EstadoRealtime>('parado')
  const [itens, setItens] = useState<Item[]>([])
  const [erro, setErro] = useState<string | null>(null)
  const sessaoRef = useRef<RealtimeSession | null>(null)
  const fimRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    fimRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [itens])

  // Encerrar ao desmontar nao e opcional: sem isto o microfone continua aberto e
  // a sessao segue sendo cobrada por minuto depois que a pessoa sai da aba.
  useEffect(() => () => sessaoRef.current?.desconectar(), [])

  const parar = useCallback(() => {
    sessaoRef.current?.desconectar()
    sessaoRef.current = null
  }, [])

  const comecar = useCallback(async () => {
    setErro(null)
    setItens([])
    const sessao = new RealtimeSession({
      onEstado: setEstado,
      onFala: (f) =>
        setItens((xs) => [...xs, { tipo: 'fala', id: f.id, quem: f.quem, texto: f.texto }]),
      onBusca: (busca) =>
        setItens((xs) => [
          ...xs,
          { tipo: 'busca', id: `busca-${xs.length}-${Date.now()}`, busca },
        ]),
      onErro: setErro,
    })
    sessaoRef.current = sessao
    try {
      await sessao.conectar()
    } catch (e) {
      setErro(e instanceof Error ? e.message : 'Could not start the conversation')
      parar()
    }
  }, [parar])

  const ativo = estado !== 'parado'

  return (
    <div className="flex flex-col gap-4 rounded-lg border bg-card p-4">
      <div className="flex items-center gap-3">
        <Button
          onClick={ativo ? parar : comecar}
          disabled={!pronto && !ativo}
          variant={ativo ? 'destructive' : 'default'}
          className="gap-2"
        >
          {ativo ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          {ativo ? 'End conversation' : 'Talk to your archive'}
        </Button>

        <span
          className={cn(
            'text-sm text-muted-foreground',
            estado === 'buscando' && 'text-foreground'
          )}
        >
          {ROTULO[estado]}
        </span>

        {estado === 'ouvindo' && (
          <span className="ml-auto flex h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
        )}
      </div>

      {!pronto && (
        <p className="text-sm text-muted-foreground">
          Upload a document first — the agent only answers from your archive.
        </p>
      )}

      {erro && <p className="text-sm text-destructive">{erro}</p>}

      {itens.length > 0 && (
        <div className="max-h-80 space-y-3 overflow-y-auto pr-1">
          {itens.map((item) =>
            item.tipo === 'fala' ? (
              <div
                key={item.id}
                className={cn(
                  'text-sm',
                  item.quem === 'pessoa' ? 'text-foreground' : 'text-muted-foreground'
                )}
              >
                <span className="mr-2 text-xs uppercase tracking-wide opacity-60">
                  {item.quem === 'pessoa' ? 'you' : 'agent'}
                </span>
                {item.texto}
              </div>
            ) : (
              <TrilhaDaBusca key={item.id} busca={item.busca} />
            )
          )}
          <div ref={fimRef} />
        </div>
      )}
    </div>
  )
}

/**
 * Uma busca na linha do tempo. E a prova de que a resposta veio do acervo, e o
 * unico lugar onde a divergencia entre fontes fica escrita: o agente tambem a
 * diz em voz alta, mas som nao se relê.
 */
function TrilhaDaBusca({ busca }: { busca: BuscaNaConversa }) {
  return (
    <div className="rounded-md border border-dashed bg-muted/40 p-3 text-sm">
      <div className="flex items-start gap-2">
        <Search className="mt-0.5 h-3.5 w-3.5 shrink-0 opacity-60" />
        <div className="flex-1">
          <span className="opacity-70">searched</span>{' '}
          <span className="font-medium">“{busca.pergunta}”</span>
          <span className="ml-2 text-xs opacity-60">
            {busca.trechos} {busca.trechos === 1 ? 'passage' : 'passages'} kept
          </span>
        </div>
      </div>

      {busca.dataDeReferencia && (
        <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
          <CalendarClock className="h-3.5 w-3.5 shrink-0" />
          answered as of {busca.dataDeReferencia}
        </p>
      )}

      {busca.divergencia && (
        <div className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          <p className="flex items-center gap-2 font-medium text-amber-700 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            Your documents disagree
          </p>
          <p className="mt-1 text-muted-foreground">{busca.divergencia.summary}</p>
          {busca.divergencia.sources.length > 0 && (
            <ul className="mt-1 list-inside list-disc text-muted-foreground">
              {busca.divergencia.sources.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!busca.divergencia && busca.trechos === 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          Nothing in the archive answered this — the agent should say so instead of guessing.
        </p>
      )}
    </div>
  )
}
