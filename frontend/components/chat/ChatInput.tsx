'use client'

import { useState, useRef, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Send, Square, Paperclip } from 'lucide-react'

interface ChatInputProps {
  onSend: (message: string) => void
  isLoading: boolean
  onStop?: () => void
  placeholder?: string
  disabled?: boolean
}

export function ChatInput({
  onSend,
  isLoading,
  onStop,
  placeholder = 'Ask a question about your documents...',
  disabled,
}: ChatInputProps) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
    }
  }, [input])

  const handleSubmit = () => {
    if (!input.trim() || isLoading || disabled) return
    onSend(input)
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="border-t bg-background p-4">
      <div className="mx-auto max-w-3xl">
        <div className="relative flex items-end gap-2 rounded-2xl border bg-background p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            rows={1}
            className={cn(
              'flex-1 resize-none bg-transparent px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-50',
              'min-h-[40px] max-h-[200px]'
            )}
          />

          <div className="flex items-center gap-1">
            {isLoading ? (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                onClick={onStop}
                className="h-9 w-9 rounded-xl hover:bg-destructive/10 hover:text-destructive"
              >
                <Square className="h-4 w-4 fill-current" />
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                onClick={handleSubmit}
                disabled={!input.trim() || disabled}
                className="h-9 w-9 rounded-xl"
              >
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>

        <p className="mt-2 text-center text-xs text-muted-foreground">
          Press Enter to send, Shift+Enter for new line
        </p>
      </div>
    </div>
  )
}
