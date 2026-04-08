export interface Citation {
  document_id: string
  document_title: string
  page: number
  snippet: string
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
  timestamp: Date
}

export interface ThreadSummary {
  id: string
  title: string
  updated_at: string
}

export interface SSEEvent {
  type: 'workflow' | 'sources' | 'chunk' | 'done' | 'error'
  data: any
}
