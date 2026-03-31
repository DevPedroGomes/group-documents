'use client'

import { motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { Bot, User, FileText } from 'lucide-react'
import type { Message, Citation } from '@/hooks/useChat'

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
      <Avatar className={cn('h-8 w-8 shrink-0', isUser ? 'bg-primary' : 'bg-muted')}>
        <AvatarFallback className={cn(isUser ? 'bg-primary text-primary-foreground' : 'bg-muted')}>
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      <div className={cn('flex flex-col gap-2 max-w-[80%]', isUser ? 'items-end' : 'items-start')}>
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-sm',
            isUser
              ? 'bg-primary text-primary-foreground rounded-tr-sm'
              : 'bg-muted rounded-tl-sm'
          )}
        >
          <div className={cn('prose prose-sm max-w-none', isUser ? 'prose-invert' : '')}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
        </div>

        {message.citations && message.citations.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-1">
            {message.citations.map((citation, idx) => (
              <button
                key={idx}
                onClick={() => onCitationClick?.(citation)}
                className="group flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-secondary/50 hover:bg-secondary text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <FileText className="h-3 w-3" />
                <span className="max-w-[150px] truncate">{citation.document_title}</span>
                <Badge variant="outline" className="h-4 px-1 text-[10px]">
                  p.{citation.page}
                </Badge>
              </button>
            ))}
          </div>
        )}

        <span className="text-[10px] text-muted-foreground px-1">
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
      <Avatar className="h-8 w-8 shrink-0 bg-muted">
        <AvatarFallback className="bg-muted">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>

      <div className="flex flex-col gap-2">
        <div className="rounded-2xl rounded-tl-sm px-4 py-3 bg-muted">
          <div className="flex items-center gap-2">
            <div className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <motion.div
                  key={i}
                  className="w-2 h-2 bg-muted-foreground/50 rounded-full"
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
            <span className="text-sm text-muted-foreground">Thinking...</span>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
