// Contrato do SSE de /chat e da trilha de decisao. Linhas antigas do banco nao
// tem `kind`, `url`, `document_date`, `queries` nem `vigente`: todo campo que
// chegou depois e opcional aqui, e quem exibe trata a ausencia.

export type CitationKind = 'document' | 'web'

export interface Citation {
  kind?: CitationKind
  /** Nulo em resultado web: nao e documento do acervo. */
  document_id: string | null
  document_title: string
  page: number | null
  /** Ate 300 caracteres do trecho usado na resposta. */
  snippet: string
  /** Data efetiva do documento, "YYYY-MM-DD". */
  document_date?: string | null
  url?: string | null
}

/** Citacao gravada antes do `kind` existir: web e a que tem URL e nao tem documento. */
export function tipoDaCitacao(c: Citation): CitationKind {
  if (c.kind) return c.kind
  return c.url && !c.document_id ? 'web' : 'document'
}

export interface ConflictEvent {
  summary: string
  sources: string[]
  /** Titulo da fonte com a data de documento mais recente; decidido no backend. */
  vigente?: string | null
}

export interface DoneEvent {
  thread_id: string
  message_id: string | null
  low_confidence: boolean
}

export interface WorkflowStep {
  step: string
  status: 'in_progress' | 'completed'
  details: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  /** Pode chegar antes, entre ou depois dos tokens; fica na mensagem que o recebeu. */
  conflict?: ConflictEvent | null
  lowConfidence?: boolean
  /** Id da resposta gravada, vindo no `done`. */
  messageId?: string | null
  timestamp: Date
}

export interface ThreadSummary {
  id: string
  title: string
  updated_at: string
}

export type SSEEvent =
  | { type: 'workflow'; data: WorkflowStep[] }
  | { type: 'sources'; data: Citation[] }
  | { type: 'conflict'; data: ConflictEvent }
  | { type: 'chunk'; data: string }
  | { type: 'done'; data: DoneEvent }
  | { type: 'error'; data: { message: string } }

/** Um trecho na trilha: sem o texto, que vive em `chunks`. */
export interface DecisionPassage {
  document_id: string | null
  document_title: string | null
  page: number | null
  score: number
  score_scale?: string
  document_date?: string | null
  url?: string | null
}

export interface Decision {
  id: string
  thread_id: string | null
  message_id: string | null
  question: string
  /** As consultas de fato buscadas; `[]` em linha antiga. */
  queries?: string[]
  retrieved?: DecisionPassage[] | null
  graded?: DecisionPassage[] | null
  considered: number
  kept: number
  score_scale: string
  /** Texto de tela, em ingles, vindo do backend. */
  score_scale_hint: string
  reranked: boolean
  low_confidence: boolean
  web_used: boolean
  answered: boolean
  conflict: ConflictEvent | null
  as_of: string | null
  latency_ms: number | null
  created_at: string | null
}

/** Item de GET /documents. */
export interface LibraryDocument {
  id: string
  title: string
  mime: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  /** Causa da falha, de meta->>'error'. Sem ela o badge "Failed" e um beco sem saida. */
  erro?: string | null
  /** Em pending/processing ha mais de 30 min: a fila perdeu o job. */
  preso?: boolean
  /** Data em que o documento vale, "YYYY-MM-DD"; e ela que o "Answer as of" usa. */
  effective_date?: string | null
  summary?: string
  chunk_count?: number
}
