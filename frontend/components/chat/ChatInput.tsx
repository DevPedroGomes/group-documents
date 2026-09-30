'use client'

import { useState, useRef, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { CalendarClock, Send, Square, X } from 'lucide-react'

interface ChatInputProps {
  onSend: (message: string) => void
  isLoading: boolean
  onStop?: () => void
  placeholder?: string
  disabled?: boolean
  /** Recorte no tempo, "YYYY-MM-DD"; vazio = o acervo de hoje. */
  asOf: string
  onAsOfChange: (value: string) => void
}

export function ChatInput({
  onSend,
  isLoading,
  onStop,
  placeholder = 'Ask a question about your documents...',
  disabled,
  asOf,
  onAsOfChange,
}: ChatInputProps) {
  const [input, setInput] = useState('')
  const [editandoData, setEditandoData] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const dataRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
    }
  }, [input])

  useEffect(() => {
    if (editandoData) dataRef.current?.focus()
  }, [editandoData])

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

  const limparData = () => {
    onAsOfChange('')
    setEditandoData(false)
  }

  const recorteVisivel = editandoData || Boolean(asOf)

  return (
    <div className="border-t border-white/10 glass-panel px-3 py-3 sm:p-4">
      <div className="mx-auto max-w-3xl">
        <div className="relative flex items-end gap-2 rounded-2xl bg-white/5 ring-1 ring-white/10 backdrop-blur p-2 focus-within:ring-2 focus-within:ring-blue-400/50 transition-all">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            rows={1}
            aria-label="Your question"
            className={cn(
              'flex-1 resize-none bg-transparent px-3 py-2 text-sm text-white placeholder:text-neutral-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50',
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
                className="h-9 w-9 rounded-xl hover:bg-red-500/10 hover:text-red-300"
                aria-label="Stop generating"
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
                aria-label="Send"
              >
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5">
          {/* O recorte no tempo existia no backend e nao tinha controle na tela:
              "e em marco de 2025?" so era possivel pela voz. */}
          {recorteVisivel ? (
            <div
              className={cn(
                'flex items-center gap-2 rounded-full py-1 pl-3 pr-1 ring-1 transition-colors',
                asOf ? 'bg-blue-400/10 ring-blue-400/40' : 'bg-white/5 ring-white/10'
              )}
            >
              <CalendarClock className="h-3.5 w-3.5 shrink-0 text-blue-300" aria-hidden />
              <label htmlFor="answer-as-of" className="text-xs text-neutral-300 whitespace-nowrap">
                Answer as of
              </label>
              <input
                id="answer-as-of"
                ref={dataRef}
                type="date"
                // Sem teto o Chrome aceita ano de 5 digitos, que o backend recusa.
                min="1900-01-01"
                max="9999-12-31"
                value={asOf}
                onChange={(e) => onAsOfChange(e.target.value)}
                onBlur={() => {
                  if (!asOf) setEditandoData(false)
                }}
                className="bg-transparent text-xs font-mono text-white [color-scheme:dark] focus:outline-none"
              />
              <button
                type="button"
                onClick={limparData}
                className="flex h-6 w-6 items-center justify-center rounded-full text-neutral-400 hover:bg-white/10 hover:text-white"
                aria-label="Clear the date and answer with today's library"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setEditandoData(true)}
              className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs text-neutral-400 ring-1 ring-white/10 transition-colors hover:bg-white/5 hover:text-white"
            >
              <CalendarClock className="h-3.5 w-3.5" aria-hidden />
              Answer as of a date
            </button>
          )}

          <p className="hidden sm:block text-[10px] text-neutral-500 uppercase tracking-widest font-medium">
            Enter to send · Shift+Enter for new line
          </p>
        </div>

        {asOf && (
          <p className="mt-1.5 px-1 text-[11px] text-neutral-400">
            Only documents dated on or before {asOf} are searched: their document date, or the upload date when none was set.
          </p>
        )}
      </div>
    </div>
  )
}
