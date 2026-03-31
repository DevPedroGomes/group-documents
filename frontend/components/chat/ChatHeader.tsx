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
    <header className="sticky top-0 z-10 flex items-center justify-between border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 px-4 py-3">
      <div className="flex items-center gap-3">
        <Link href="/">
          <Button variant="ghost" size="icon" className="h-9 w-9 rounded-xl">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>

        <div className="flex flex-col">
          <h1 className="text-lg font-semibold">AI Assistant</h1>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="gap-1 text-xs">
              <FileText className="h-3 w-3" />
              {documentCount} {documentCount === 1 ? 'document' : 'documents'}
            </Badge>
          </div>
        </div>
      </div>

      {hasMessages && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onReset}
          className="gap-2 text-muted-foreground hover:text-foreground"
        >
          <RotateCcw className="h-4 w-4" />
          New chat
        </Button>
      )}
    </header>
  )
}
