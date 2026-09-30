'use client'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArrowLeft, RotateCcw, FileText, Library, MessageSquare } from 'lucide-react'
import Link from 'next/link'

interface ChatHeaderProps {
  /** Documentos selecionados; 0 = a pergunta vai para o acervo inteiro. */
  documentCount: number
  onReset: () => void
  hasMessages: boolean
  historyOpen: boolean
  onToggleHistory: () => void
}

export function ChatHeader({
  documentCount,
  onReset,
  hasMessages,
  historyOpen,
  onToggleHistory,
}: ChatHeaderProps) {
  return (
    <header className="sticky top-0 z-10 border-b border-white/10 glass-panel">
      <div className="flex items-center justify-between gap-2 px-3 sm:px-4 py-3">
        <div className="flex min-w-0 items-center gap-1.5 sm:gap-3">
          <Button
            asChild
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0 rounded-full"
          >
            <Link href="/library" aria-label="Back to the library">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0 rounded-full"
            onClick={onToggleHistory}
            aria-label={historyOpen ? 'Hide conversation history' : 'Show conversation history'}
            aria-expanded={historyOpen}
          >
            <MessageSquare className="h-4 w-4" />
          </Button>

          <div className="flex min-w-0 flex-col">
            <h1 className="text-lg font-semibold text-white tracking-tight">AI Assistant</h1>
            <Badge variant="secondary" className="gap-1 text-[10px] uppercase tracking-widest w-fit">
              {documentCount > 0 ? (
                <>
                  <FileText className="h-3 w-3" />
                  {documentCount} {documentCount === 1 ? 'doc' : 'docs'}
                </>
              ) : (
                <>
                  <Library className="h-3 w-3" />
                  whole library
                </>
              )}
            </Badge>
          </div>
        </div>

        {hasMessages && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onReset}
            className="shrink-0 gap-2 text-neutral-400 hover:text-white"
            aria-label="New chat"
          >
            <RotateCcw className="h-4 w-4" />
            <span className="hidden sm:inline">New chat</span>
          </Button>
        )}
      </div>
    </header>
  )
}
