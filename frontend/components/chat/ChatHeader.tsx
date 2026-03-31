'use client'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArrowLeft, RotateCcw, FileText } from 'lucide-react'
import Link from 'next/link'

interface ChatHeaderProps {
  documentCount: number
  onReset: () => void
  hasMessages: boolean
}

export function ChatHeader({ documentCount, onReset, hasMessages }: ChatHeaderProps) {
  return (
    <header className="sticky top-0 z-10 border-b border-white/20 glass-panel">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          <Link href="/">
            <Button variant="ghost" size="icon" className="h-9 w-9 rounded-full">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>

          <div className="flex flex-col">
            <h1 className="text-lg font-semibold text-zinc-900 tracking-tight">AI Assistant</h1>
            <Badge variant="secondary" className="gap-1 text-[10px] uppercase tracking-widest w-fit">
              <FileText className="h-3 w-3" />
              {documentCount} {documentCount === 1 ? 'doc' : 'docs'}
            </Badge>
          </div>
        </div>

        {hasMessages && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onReset}
            className="gap-2 text-zinc-400 hover:text-zinc-900"
          >
            <RotateCcw className="h-4 w-4" />
            New chat
          </Button>
        )}
      </div>
    </header>
  )
}
