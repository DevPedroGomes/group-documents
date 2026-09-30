'use client'

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'

// Tipos que o backend serve sem nome util quando o storage_path perde a
// extensao. Nesses casos o tipo certo vem de quem abriu o preview.
const TIPOS_GENERICOS = new Set(['', 'application/octet-stream', 'binary/octet-stream'])

/**
 * Preview de um documento do acervo. Usado pela biblioteca e pelas citacoes
 * do chat; na citacao, `page` abre o PDF na pagina citada.
 */
export function DocumentPreview({
  id,
  mime,
  title,
  page,
  getToken,
  onClose,
}: {
  id: string
  /** Tipo gravado no documento. Ausente (citacao), vale o que o backend mandar. */
  mime?: string
  title?: string
  page?: number | null
  getToken: () => Promise<string | undefined>
  onClose: () => void
}) {
  const [url, setUrl] = useState('')
  const [tipo, setTipo] = useState(mime ?? '')
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)

  // O backend responde o arquivo em si (FileResponse), nao um JSON com URL
  // assinada. Como a rota exige Authorization, o <iframe>/<img> nao pode
  // apontar direto para ela: o corpo e baixado e servido por um blob: local,
  // revogado ao fechar.
  useEffect(() => {
    let objectUrl = ''
    let cancelled = false

    ;(async () => {
      try {
        const token = await getToken()
        const res = await fetch(`/api/document/${encodeURIComponent(id)}/preview`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok) throw new Error(`preview failed: ${res.status}`)
        const raw = await res.blob()
        if (cancelled) return
        // O mime gravado no documento e a fonte confiavel; sem ele, o do
        // backend; generico e com pagina citada, so pode ser PDF.
        let resolvido = mime || raw.type
        if (TIPOS_GENERICOS.has(resolvido) && page != null) resolvido = 'application/pdf'
        const blob = resolvido && resolvido !== raw.type ? new Blob([raw], { type: resolvido }) : raw
        objectUrl = URL.createObjectURL(blob)
        setTipo(resolvido)
        setUrl(objectUrl)
      } catch {
        if (!cancelled) setError(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [id, mime, page, getToken])

  useEffect(() => {
    const aoTeclar = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', aoTeclar)
    return () => window.removeEventListener('keydown', aoTeclar)
  }, [onClose])

  const renderPreview = () => {
    if (loading) {
      return (
        <div className="flex items-center justify-center h-full">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (error || !url) {
      return (
        <div className="flex items-center justify-center h-full px-6 text-center text-sm text-muted-foreground">
          Could not load a preview for this file.
        </div>
      )
    }

    if (tipo.startsWith('audio/')) {
      return (
        <div className="flex items-center justify-center h-full">
          <audio controls src={url} className="w-full max-w-md" />
        </div>
      )
    }

    if (tipo.startsWith('video/')) {
      return <video controls src={url} className="w-full h-full object-contain" />
    }

    if (tipo.startsWith('image/')) {
      return (
        <div className="flex items-center justify-center h-full p-4">
          <img src={url} alt={title || 'Preview'} className="max-w-full max-h-full object-contain rounded-lg" />
        </div>
      )
    }

    // `#page=N` e lido pelo visualizador de PDF do navegador, inclusive em blob:.
    const alvo = tipo === 'application/pdf' && page != null ? `${url}#page=${page}` : url
    return <iframe src={alvo} title={title || 'Document preview'} className="w-full h-full border-none bg-white" />
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 bg-neutral-950/85 backdrop-blur-sm flex items-center justify-center p-3 sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title ? `Preview of ${title}` : 'Document preview'}
    >
      <motion.div
        initial={{ scale: 0.96, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.96, opacity: 0 }}
        className="relative flex flex-col w-full h-full max-w-6xl max-h-[92vh] bg-neutral-900 ring-1 ring-white/10 rounded-3xl overflow-hidden shadow-2xl shadow-black/60"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-white/10 pl-5 pr-3 py-2.5">
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-white">
            {title || 'Preview'}
          </p>
          {page != null && (
            <span className="shrink-0 rounded-full bg-blue-400/15 px-2.5 py-0.5 text-[10px] font-mono uppercase tracking-widest text-blue-200">
              page {page}
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0 rounded-full"
            onClick={onClose}
            aria-label="Close preview"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="min-h-0 flex-1">{renderPreview()}</div>
      </motion.div>
    </motion.div>
  )
}
