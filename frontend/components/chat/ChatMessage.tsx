'use client'

import { motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import { Bot, User, FileText } from 'lucide-react'
import type { Message, Citation } from '@/lib/types'

interface ChatMessageProps {
  message: Message
  onCitationClick?: (citation: Citation) => void
}

export function ChatMessage({ message, onCitationClick }: ChatMessageProps) {
  const isUser = message.role === 'user'

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
      <Avatar className={cn('h-8 w-8 shrink-0', isUser ? 'btn-primary-gradient shadow-sm' : 'bg-zinc-100')}>
        <AvatarFallback className={cn(isUser ? 'bg-transparent text-zinc-900' : 'bg-zinc-100 text-zinc-600')}>
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      <div className={cn('flex flex-col gap-2 max-w-[80%]', isUser ? 'items-end' : 'items-start')}>
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-sm',
            isUser
              ? 'bg-zinc-900 text-white rounded-tr-sm'
              : 'bg-white border border-zinc-200 shadow-sm rounded-tl-sm text-zinc-700'
          )}
        >
          <div className={cn('prose prose-sm max-w-none', isUser ? 'prose-invert' : 'prose-zinc')}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
        </div>

        {message.citations && message.citations.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-1">
            {message.citations.map((citation, idx) => (
              <button
                key={idx}
                onClick={() => onCitationClick?.(citation)}
                className="group flex items-center gap-1.5 px-3 py-1 rounded-full bg-orange-50 border border-orange-200 text-xs text-orange-700 hover:bg-orange-100 transition-colors"
              >
                <FileText className="h-3 w-3" />
                <span className="max-w-[150px] truncate">{citation.document_title}</span>
                <span className="text-[10px] font-semibold uppercase tracking-wider text-orange-500">
                  p.{citation.page}
                </span>
              </button>
            ))}
          </div>
        )}

        <span className="text-[10px] text-zinc-400 px-1 font-medium">
          {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
    </motion.div>
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
      <Avatar className="h-8 w-8 shrink-0 bg-zinc-100">
        <AvatarFallback className="bg-zinc-100 text-zinc-600">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>

      <div className="flex flex-col gap-2">
        <div className="rounded-2xl rounded-tl-sm px-4 py-3 bg-white border border-zinc-200 shadow-sm">
          <div className="flex items-center gap-2">
            <div className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <motion.div
                  key={i}
                  className="w-2 h-2 bg-orange-400 rounded-full"
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
            <span className="text-sm text-zinc-400">Thinking...</span>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
