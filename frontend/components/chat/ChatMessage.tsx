'use client'

import { motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import { AlertTriangle, Bot, User, FileText, Globe, ArrowUpRight, CircleDashed } from 'lucide-react'
import { tipoDaCitacao, type Message, type Citation, type ConflictEvent } from '@/lib/types'

interface ChatMessageProps {
  message: Message
  /** Abre o documento citado no preview. So recebe citacoes do acervo. */
  onCitationClick?: (citation: Citation) => void
}

export function ChatMessage({ message, onCitationClick }: ChatMessageProps) {
  const isUser = message.role === 'user'
  const citacoes = message.citations ?? []

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className={cn(
        'flex gap-3 px-4 py-3',
        isUser ? 'flex-row-reverse' : 'flex-row'
      )}
    >
      <Avatar className={cn('h-8 w-8 shrink-0', isUser ? 'bg-white' : 'bg-white/5 ring-1 ring-white/10')}>
        <AvatarFallback className={cn(isUser ? 'bg-transparent text-neutral-900' : 'bg-transparent text-blue-300')}>
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      <div
        className={cn(
          'flex min-w-0 flex-col gap-2',
          isUser ? 'max-w-[80%] items-end' : 'max-w-[calc(100%-2.75rem)] sm:max-w-[85%] items-start'
        )}
      >
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-sm',
            isUser
              ? 'bg-white text-neutral-900 rounded-tr-sm'
              : 'bg-white/5 ring-1 ring-white/10 backdrop-blur rounded-tl-sm text-neutral-100'
          )}
        >
          <div className={cn('prose prose-sm max-w-none break-words', isUser ? 'prose-zinc' : 'prose-invert')}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
        </div>

        {!isUser && message.conflict && <AvisoDeDivergencia conflito={message.conflict} />}

        {!isUser && citacoes.length > 0 && (
          <div className="w-full">
            <p className="mb-1.5 px-1 text-[10px] font-mono uppercase tracking-widest text-neutral-400">
              Sources · {citacoes.length}
            </p>
            <ul className="grid gap-1.5 sm:grid-cols-2">
              {citacoes.map((citation, idx) => (
                <li key={idx} className="min-w-0">
                  <CartaoDeCitacao citation={citation} onOpen={onCitationClick} />
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 px-1">
          <span className="text-[10px] text-neutral-400 font-medium">
            {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
          {!isUser && message.lowConfidence && (
            <span className="inline-flex items-center gap-1.5 text-[11px] text-neutral-400">
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-200">
                <CircleDashed className="h-3 w-3" aria-hidden />
                Low confidence
              </span>
              few or weak matches in your documents
            </span>
          )}
        </div>
      </div>
    </motion.div>
  )
}

/**
 * A divergencia entre fontes, na propria resposta. Antes ela so existia como
 * uma linha no painel de passos, que some quando o stream termina.
 */
function AvisoDeDivergencia({ conflito }: { conflito: ConflictEvent }) {
  const fontes = Array.isArray(conflito.sources) ? conflito.sources : []
  return (
    <div role="note" className="w-full rounded-xl border border-amber-400/30 bg-amber-400/[0.07] px-3.5 py-3 text-xs">
      <p className="flex items-center gap-2 font-semibold text-amber-200">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
        Your sources disagree
      </p>
      <p className="mt-1.5 leading-relaxed text-neutral-200">{conflito.summary}</p>
      {fontes.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {fontes.map((f) => (
            <li
              key={f}
              className={cn(
                'max-w-full truncate rounded-full px-2.5 py-0.5 text-[11px] ring-1',
                f === conflito.vigente
                  ? 'bg-emerald-400/10 text-emerald-200 ring-emerald-400/30'
                  : 'bg-white/5 text-neutral-300 ring-white/10'
              )}
            >
              {f}
              {f === conflito.vigente && <span className="ml-1.5 font-semibold">· current</span>}
            </li>
          ))}
        </ul>
      )}
      {conflito.vigente && (
        <p className="mt-2 text-[11px] leading-relaxed text-neutral-400">
          <span className="text-emerald-200">{conflito.vigente}</span> has the most recent document date
          among the sources that disagree.
        </p>
      )}
    </div>
  )
}

/** So http(s): a URL vem de resultado de busca web, e `javascript:` viraria XSS. */
function urlSegura(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const u = new URL(url)
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.toString() : null
  } catch {
    return null
  }
}

function dominio(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function CartaoDeCitacao({
  citation,
  onOpen,
}: {
  citation: Citation
  onOpen?: (citation: Citation) => void
}) {
  const web = tipoDaCitacao(citation) === 'web'
  const base =
    'group flex h-full w-full flex-col gap-1 rounded-xl px-3 py-2 text-left ring-1 transition-colors'

  if (web) {
    const href = urlSegura(citation.url)
    const corpo = (
      <>
        <span className="flex min-w-0 items-center gap-1.5">
          <Globe className="h-3.5 w-3.5 shrink-0 text-violet-300" aria-hidden />
          <span className="shrink-0 rounded bg-violet-400/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-widest text-violet-200">
            web
          </span>
          <span className="min-w-0 truncate text-xs font-medium text-neutral-100">
            {citation.document_title || (href ? dominio(href) : 'Web result')}
          </span>
          {href && <ArrowUpRight className="ml-auto h-3.5 w-3.5 shrink-0 text-neutral-500 group-hover:text-white" aria-hidden />}
        </span>
        {href && <span className="truncate text-[10px] font-mono text-neutral-400">{dominio(href)}</span>}
        {citation.snippet && (
          <span className="line-clamp-2 text-[11px] leading-snug text-neutral-400">{citation.snippet}</span>
        )}
      </>
    )
    return href ? (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(base, 'bg-violet-400/[0.06] ring-violet-400/20 hover:bg-violet-400/[0.12]')}
        aria-label={`Web source: ${citation.document_title || dominio(href)} (opens in a new tab)`}
      >
        {corpo}
      </a>
    ) : (
      <div className={cn(base, 'bg-violet-400/[0.06] ring-violet-400/20')}>{corpo}</div>
    )
  }

  const abrivel = Boolean(citation.document_id && onOpen)
  const corpo = (
    <>
      <span className="flex min-w-0 items-center gap-1.5">
        <FileText className="h-3.5 w-3.5 shrink-0 text-blue-300" aria-hidden />
        <span className="min-w-0 truncate text-xs font-medium text-neutral-100">{citation.document_title}</span>
        {citation.page != null && (
          <span className="ml-auto shrink-0 text-[10px] font-semibold uppercase tracking-wider text-blue-300">
            p.{citation.page}
          </span>
        )}
      </span>
      {citation.document_date && (
        <span className="text-[10px] font-mono text-neutral-400">dated {citation.document_date}</span>
      )}
      {citation.snippet && (
        <span className="line-clamp-2 text-[11px] leading-snug text-neutral-400">{citation.snippet}</span>
      )}
    </>
  )
  return abrivel ? (
    <button
      type="button"
      onClick={() => onOpen?.(citation)}
      className={cn(base, 'bg-blue-400/[0.07] ring-blue-400/20 hover:bg-blue-400/[0.14] hover:ring-blue-400/40')}
      aria-label={`Open ${citation.document_title}${citation.page != null ? ` at page ${citation.page}` : ''}`}
    >
      {corpo}
    </button>
  ) : (
    <div className={cn(base, 'bg-blue-400/[0.07] ring-blue-400/20')}>{corpo}</div>
  )
}

export function ThinkingMessage() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="flex gap-3 px-4 py-3"
    >
      <Avatar className="h-8 w-8 shrink-0 bg-white/5 ring-1 ring-white/10">
        <AvatarFallback className="bg-transparent text-blue-300">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>

      <div className="flex flex-col gap-2">
        <div className="rounded-2xl rounded-tl-sm px-4 py-3 bg-white/5 ring-1 ring-white/10 backdrop-blur">
          <div className="flex items-center gap-2">
            <div className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <motion.div
                  key={i}
                  className="w-2 h-2 bg-blue-400 rounded-full"
                  animate={{
                    scale: [1, 1.3, 1],
                    opacity: [0.5, 1, 0.5],
                  }}
                  transition={{
                    duration: 1,
                    repeat: Infinity,
                    delay: i * 0.2,
                  }}
                />
              ))}
            </div>
            <span className="text-sm text-neutral-400">Thinking...</span>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
