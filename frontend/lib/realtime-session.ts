/**
 * Conversa por voz continua sobre o acervo, via WebRTC.
 *
 * Migrado do `voice_rag` em 06/09/2026. La a camada estava certa e nao tinha por
 * que existir: um chat de provedor faz voz sobre um PDF melhor do que nos. Aqui
 * ela tem, porque aqui existe acervo, isolamento por usuario e trilha de decisao.
 *
 * O audio NAO passa pelo nosso backend. Proxiar midia em tempo real numa VPS de
 * 2 vCPU seria o gargalo, e o WebRTC ja resolve jitter, perda de pacote e eco. O
 * backend entra so onde precisa mandar: cunhar a credencial efemera (a chave real
 * da OpenAI nunca chega ao navegador) e executar a busca, para que quem decide o
 * que e relevante continue sendo o grader do servidor.
 *
 * ⚠️ CSP: a troca de SDP e feita DIRETO com api.openai.com. Se `connect-src` no
 * docker-compose deixar de permitir esse host, o botao falha para 100% dos
 * visitantes, em silencio e sem erro no servidor. Ja aconteceu uma vez no
 * voice_rag (commit 21e3642). A CSP daqui hoje e `connect-src 'self' https: wss:`,
 * que cobre; nao aperte sem lembrar disto.
 */

import { fetchWithAuth } from '@/lib/auth'

const CALLS_URL = 'https://api.openai.com/v1/realtime/calls'

export type EstadoRealtime = 'parado' | 'conectando' | 'ouvindo' | 'buscando' | 'falando'

export type FalaNaConversa = {
  id: string
  quem: 'pessoa' | 'agente'
  texto: string
}

/** O que uma busca produziu. E o que o painel de trilha mostra ao vivo. */
export type BuscaNaConversa = {
  pergunta: string
  trechos: number
  baixaConfianca: boolean
  /** Preenchido quando duas ou mais fontes respondem diferente a mesma pergunta. */
  divergencia: { summary: string; sources: string[] } | null
  dataDeReferencia: string | null
}

type Callbacks = {
  onEstado?: (estado: EstadoRealtime) => void
  onFala?: (fala: FalaNaConversa) => void
  onBusca?: (busca: BuscaNaConversa) => void
  onErro?: (mensagem: string) => void
}

export class RealtimeSession {
  private pc: RTCPeerConnection | null = null
  private dc: RTCDataChannel | null = null
  private stream: MediaStream | null = null
  private audio: HTMLAudioElement | null = null
  private threadId: string | null = null
  private readonly cb: Callbacks

  constructor(cb: Callbacks = {}) {
    this.cb = cb
  }

  /** A thread desta conversa. A trilha de decisao fica agrupada por ela. */
  get thread(): string | null {
    return this.threadId
  }

  async conectar(): Promise<void> {
    this.cb.onEstado?.('conectando')

    // 1. Credencial efemera + a thread da conversa. O backend cunha ja amarrada
    //    as instrucoes e a tool; o navegador nunca ve a chave real.
    const r = await fetchWithAuth('/api/realtime/session', { method: 'POST' })
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: 'Realtime is unavailable' }))
      throw new Error(e.detail ?? 'Realtime is unavailable')
    }
    const { client_secret, thread_id } = (await r.json()) as {
      client_secret: string
      thread_id: string
    }
    this.threadId = thread_id

    // 2. Microfone. Sem echoCancellation o modelo escuta a propria voz pelo
    //    alto-falante e se interrompe sozinho num loop.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })

    const pc = new RTCPeerConnection()
    this.pc = pc

    this.audio = new Audio()
    this.audio.autoplay = true
    pc.ontrack = (ev) => {
      if (this.audio) this.audio.srcObject = ev.streams[0]
    }
    this.stream.getTracks().forEach((t) => pc.addTrack(t, this.stream!))

    // 3. Data channel: tudo que nao e audio (transcricoes, chamadas de tool).
    const dc = pc.createDataChannel('oai-events')
    this.dc = dc
    dc.addEventListener('message', (ev) => {
      void this.aoEvento(JSON.parse(ev.data))
    })

    // 4. Troca de SDP direto com a OpenAI, autenticada pela credencial efemera.
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    const resp = await fetch(CALLS_URL, {
      method: 'POST',
      body: offer.sdp,
      headers: {
        Authorization: `Bearer ${client_secret}`,
        'Content-Type': 'application/sdp',
      },
    })
    if (!resp.ok) throw new Error('Could not open the voice connection')
    await pc.setRemoteDescription({ type: 'answer', sdp: await resp.text() })

    this.cb.onEstado?.('ouvindo')
  }

  private async aoEvento(ev: Record<string, unknown>): Promise<void> {
    const tipo = ev.type as string

    // Fala da pessoa, ja transcrita pelo proprio modelo.
    if (tipo === 'conversation.item.input_audio_transcription.completed') {
      const texto = String(ev.transcript ?? '').trim()
      if (texto) this.cb.onFala?.({ id: String(ev.item_id ?? Date.now()), quem: 'pessoa', texto })
      return
    }

    if (tipo === 'response.output_audio_transcript.done') {
      const texto = String(ev.transcript ?? '').trim()
      if (texto) this.cb.onFala?.({ id: String(ev.item_id ?? Date.now()), quem: 'agente', texto })
      return
    }

    if (tipo === 'output_audio_buffer.started') return this.cb.onEstado?.('falando')
    if (tipo === 'output_audio_buffer.stopped') return this.cb.onEstado?.('ouvindo')

    if (tipo === 'error') {
      this.cb.onErro?.(String((ev.error as Record<string, unknown>)?.message ?? 'Realtime error'))
      return
    }

    // O modelo pediu a busca. Chega dentro do response.done concluido.
    if (tipo === 'response.done') {
      const saidas = ((ev.response as Record<string, unknown>)?.output ?? []) as Array<
        Record<string, unknown>
      >
      for (const item of saidas) {
        if (item.type === 'function_call' && item.name === 'buscar_no_acervo') {
          await this.executarBusca(String(item.call_id), String(item.arguments ?? '{}'))
        }
      }
    }
  }

  private async executarBusca(callId: string, argumentosJson: string): Promise<void> {
    this.cb.onEstado?.('buscando')
    let pergunta = ''
    let dataDeReferencia: string | null = null
    let saida: Record<string, unknown>

    try {
      const args = JSON.parse(argumentosJson) ?? {}
      pergunta = String(args.pergunta ?? '')
      dataDeReferencia = args.data_de_referencia ? String(args.data_de_referencia) : null

      const r = await fetchWithAuth('/api/realtime/tool/buscar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pergunta,
          data_de_referencia: dataDeReferencia,
          thread_id: this.threadId,
        }),
      })
      saida = r.ok
        ? await r.json()
        : { trechos: [], baixa_confianca: true, erro: 'search failed' }
    } catch {
      // Erro da busca vira RESULTADO, nao excecao: o modelo precisa poder dizer
      // que nao conseguiu, e um tool call sem resposta trava o turno inteiro.
      saida = { trechos: [], baixa_confianca: true, erro: 'search failed' }
    }

    this.cb.onBusca?.({
      pergunta,
      trechos: (saida.trechos as unknown[] | undefined)?.length ?? 0,
      baixaConfianca: Boolean(saida.baixa_confianca),
      divergencia: (saida.divergencia as BuscaNaConversa['divergencia']) ?? null,
      dataDeReferencia,
    })

    this.enviar({
      type: 'conversation.item.create',
      item: { type: 'function_call_output', call_id: callId, output: JSON.stringify(saida) },
    })
    this.enviar({ type: 'response.create' })
    this.cb.onEstado?.('falando')
  }

  private enviar(payload: unknown): void {
    if (this.dc?.readyState === 'open') this.dc.send(JSON.stringify(payload))
  }

  desconectar(): void {
    this.stream?.getTracks().forEach((t) => t.stop())
    this.dc?.close()
    this.pc?.close()
    if (this.audio) this.audio.srcObject = null
    this.stream = null
    this.dc = null
    this.pc = null
    this.audio = null
    this.cb.onEstado?.('parado')
  }
}
