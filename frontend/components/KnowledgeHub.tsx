'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { motion, AnimatePresence } from 'framer-motion'
import { useDebounce } from '@/hooks/useDebounce'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  Search,
  Upload,
  X,
  MessageSquare,
  FileText,
  Image,
  Music,
  Video,
  File,
  Link2,
  Globe,
  CheckCircle2,
  Clock,
  Loader2,
  AlertCircle,
  Sparkles,
} from 'lucide-react'

interface Document {
  id: string
  title: string
  mime: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  summary?: string
  chunk_count?: number
}

interface KnowledgeHubProps {
  getToken: () => Promise<string | undefined>
}

export default function KnowledgeHub({ getToken }: KnowledgeHubProps) {
  const router = useRouter()
  const [docs, setDocs] = useState<Document[]>([])
  const [query, setQuery] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [isLoading, setIsLoading] = useState(true)
  const [isSearching, setIsSearching] = useState(false)
  const [uploadingCount, setUploadingCount] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [previewDoc, setPreviewDoc] = useState<{ id: string; mime: string } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [showUrlModal, setShowUrlModal] = useState(false)
  const [urlInput, setUrlInput] = useState('')
  const [isCrawling, setIsCrawling] = useState(false)
  const [urlError, setUrlError] = useState<string | null>(null)

  // Debounce search query
  const debouncedQuery = useDebounce(query, 400)

  // Fetch documents
  const fetchDocs = useCallback(async (searchQuery: string = '') => {
    setIsSearching(true)
    try {
      const token = await getToken()
      const url = searchQuery
        ? `/api/documents?semantic_query=${encodeURIComponent(searchQuery)}`
        : '/api/documents'

      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      })

      if (!res.ok) {
        throw new Error('Failed to fetch documents')
      }

      const data = await res.json()
      setDocs(data.items || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load documents')
    } finally {
      setIsLoading(false)
      setIsSearching(false)
    }
  }, [getToken])

  // Initial load
  useEffect(() => {
    fetchDocs()
  }, [fetchDocs])

  // Search on debounced query change
  useEffect(() => {
    if (!isLoading) {
      fetchDocs(debouncedQuery)
    }
  }, [debouncedQuery, fetchDocs, isLoading])

  // Poll for processing documents
  const debouncedQueryRef = useRef(debouncedQuery)
  debouncedQueryRef.current = debouncedQuery

  useEffect(() => {
    const hasPending = docs.some(d => d.status === 'pending' || d.status === 'processing')
    if (!hasPending) return

    const interval = setInterval(() => fetchDocs(debouncedQueryRef.current), 3000)
    return () => clearInterval(interval)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs.map(d => d.status).join(',')])

  // Toggle selection
  const toggleSelect = (id: string) => {
    const next = new Set(selectedIds)
    if (next.has(id)) {
      next.delete(id)
    } else {
      next.add(id)
    }
    setSelectedIds(next)
  }

  // Upload single file — backend /upload handles save + ingest in one call
  const uploadSingleFile = async (file: File, token: string): Promise<Document | null> => {
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('title', file.name)

      const uploadRes = await fetch('/api/upload', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })

      if (!uploadRes.ok) {
        const data = await uploadRes.json().catch(() => ({}))
        throw new Error(data.detail || `Failed to upload ${file.name}`)
      }

      const data = await uploadRes.json()
      return {
        id: data.document_id,
        title: file.name,
        mime: file.type,
        status: 'pending',
      }
    } catch (err) {
      console.error(`Error uploading ${file.name}:`, err)
      throw err
    }
  }

  // Upload handler - supports multiple files
  const onUpload = async (files: FileList) => {
    if (files.length === 0) return

    setError(null)
    setUploadingCount(files.length)

    try {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')

      // Upload all files in parallel
      const uploadPromises = Array.from(files).map(file =>
        uploadSingleFile(file, token)
          .then(doc => {
            // Add document to list as soon as it's uploaded
            if (doc) {
              setDocs(prev => [doc, ...prev])
            }
            setUploadingCount(prev => Math.max(0, prev - 1))
            return doc
          })
          .catch(err => {
            setUploadingCount(prev => Math.max(0, prev - 1))
            return null
          })
      )

      const results = await Promise.all(uploadPromises)
      const failed = results.filter(r => r === null).length

      if (failed > 0) {
        setError(`${failed} of ${files.length} files failed to upload`)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
      setUploadingCount(0)
    }
  }

  // Submit URL for crawl + ingest
  const submitUrl = async () => {
    const url = urlInput.trim()
    if (!url) {
      setUrlError('Enter a URL')
      return
    }
    if (!/^https?:\/\//i.test(url)) {
      setUrlError('URL must start with http:// or https://')
      return
    }

    setUrlError(null)
    setIsCrawling(true)

    try {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')

      const res = await fetch('/api/crawl', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url }),
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to crawl URL')
      }

      const data = await res.json()
      setDocs(prev => [
        {
          id: data.document_id,
          title: data.title || url,
          mime: 'text/plain',
          status: 'pending',
        },
        ...prev,
      ])
      setUrlInput('')
      setShowUrlModal(false)
    } catch (err) {
      setUrlError(err instanceof Error ? err.message : 'Crawl failed')
    } finally {
      setIsCrawling(false)
    }
  }

  // Navigate to chat
  const goToChat = () => {
    const ids = Array.from(selectedIds).join(',')
    router.push(`/chat?docs=${ids}`)
  }

  const hasDocuments = docs.length > 0
  const selectedCount = selectedIds.size

  return (
    <TooltipProvider>
      {/* Preview Modal */}
      <AnimatePresence>
        {previewDoc && (
          <PreviewModal
            id={previewDoc.id}
            mime={previewDoc.mime}
            getToken={getToken}
            onClose={() => setPreviewDoc(null)}
          />
        )}
      </AnimatePresence>

      {/* Add URL Modal */}
      <AnimatePresence>
        {showUrlModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={() => !isCrawling && setShowUrlModal(false)}
          >
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              className="w-full max-w-md rounded-xl border border-white/10 bg-neutral-900 p-6 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-3 mb-4">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400">
                  <Globe className="h-4 w-4" />
                </div>
                <div>
                  <h2 className="text-base font-semibold text-white">Add a URL</h2>
                  <p className="text-xs text-neutral-400">Crawl a web page and index its text.</p>
                </div>
              </div>

              <Input
                value={urlInput}
                onChange={(e) => { setUrlInput(e.target.value); setUrlError(null) }}
                onKeyDown={(e) => { if (e.key === 'Enter' && !isCrawling) submitUrl() }}
                placeholder="https://example.com/article"
                autoFocus
                disabled={isCrawling}
                className="h-11"
              />

              {urlError && (
                <p className="mt-2 text-xs text-red-400 flex items-center gap-1.5">
                  <AlertCircle className="h-3 w-3" />
                  {urlError}
                </p>
              )}

              <div className="mt-5 flex justify-end gap-2">
                <Button
                  variant="ghost"
                  onClick={() => { setShowUrlModal(false); setUrlInput(''); setUrlError(null) }}
                  disabled={isCrawling}
                >
                  Cancel
                </Button>
                <Button onClick={submitUrl} disabled={isCrawling} className="gap-2">
                  {isCrawling ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                  {isCrawling ? 'Crawling…' : 'Index'}
                </Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex flex-col h-[calc(100dvh-57px)] p-6 lg:p-10 max-w-7xl w-full mx-auto">
        {/* ─── Header ──────────────────────────────────────────────── */}
        <div className="mb-7">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
              Workspace / Library
            </span>
            <span className="h-px flex-1 max-w-[80px] bg-white/10" />
            <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500">
              {docs.length} indexed
            </span>
          </div>

          <div className="flex items-end justify-between gap-6">
            <div>
              <h1 className="text-3xl sm:text-4xl font-semibold tracking-tighter text-white">
                Library
              </h1>
              <p className="mt-2 text-neutral-400 text-sm max-w-md leading-relaxed">
                Search the team corpus. Select documents to take into a chat.
              </p>
            </div>

            {selectedCount > 0 && (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
              >
                <Button onClick={goToChat} className="gap-2">
                  <MessageSquare className="h-4 w-4" />
                  Talk to Agent
                  <span className="flex items-center justify-center rounded-full bg-black/15 px-2.5 py-0.5 text-xs ml-1">
                    {selectedCount}
                  </span>
                </Button>
              </motion.div>
            )}
          </div>

          {/* ─── Search + upload row ──────────────────────────────── */}
          <div className="mt-7 flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-500" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Describe what you're looking for…"
                className="pl-10 pr-9 h-11"
              />
              {query && (
                <button
                  onClick={() => setQuery('')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-500 hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
              {isSearching && (
                <div className="absolute right-10 top-1/2 -translate-y-1/2">
                  <Loader2 className="h-4 w-4 animate-spin text-neutral-500" />
                </div>
              )}
            </div>

            <input
              ref={fileRef}
              type="file"
              multiple
              accept="application/pdf,image/*,audio/*,video/*"
              className="hidden"
              onChange={(e) => {
                const files = e.target.files
                if (files && files.length > 0) onUpload(files)
                e.target.value = ''
              }}
            />
            <Button
              variant="outline"
              onClick={() => fileRef.current?.click()}
              disabled={uploadingCount > 0}
              className="gap-2 h-11"
            >
              {uploadingCount > 0 ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {uploadingCount > 0 ? `Uploading ${uploadingCount}…` : 'Upload'}
            </Button>

            <Button
              variant="outline"
              onClick={() => { setShowUrlModal(true); setUrlError(null) }}
              className="gap-2 h-11"
            >
              <Link2 className="h-4 w-4" />
              Add URL
            </Button>
          </div>

          {/* ─── Search hint ──────────────────────────────────────── */}
          {debouncedQuery && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="mt-4 inline-flex items-center gap-2 text-xs font-mono text-neutral-400"
            >
              <Sparkles className="h-3.5 w-3.5 text-blue-300" />
              <span>matching</span>
              <span className="text-white">&quot;{debouncedQuery}&quot;</span>
            </motion.div>
          )}

          {/* ─── Mono stats strip ─────────────────────────────────── */}
          {hasDocuments && (
            <div className="mt-5 flex items-center gap-4 text-[10px] font-mono uppercase tracking-widest text-neutral-500">
              <span>
                <span className="text-white/80 font-semibold">{docs.length}</span>
                <span className="ml-1.5">documents</span>
              </span>
              <span className="text-white/15">/</span>
              <span>
                <span className="text-white/80 font-semibold">{selectedCount}</span>
                <span className="ml-1.5">selected</span>
              </span>
              {selectedCount > 0 && (
                <button
                  onClick={() => setSelectedIds(new Set())}
                  className="ml-auto text-neutral-400 hover:text-white normal-case tracking-normal text-xs"
                >
                  clear selection
                </button>
              )}
            </div>
          )}
        </div>

        {/* Error */}
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-4 p-3 rounded-lg bg-red-500/10 text-red-300 text-sm flex items-center gap-2 border border-red-500/20"
          >
            <AlertCircle className="h-4 w-4" />
            {error}
            <button
              onClick={() => setError(null)}
              className="ml-auto hover:underline"
            >
              Dismiss
            </button>
          </motion.div>
        )}

        {/* ─── Documents (row list) ─────────────────────────────── */}
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="rounded-2xl bg-white/5 ring-1 ring-white/10 backdrop-blur divide-y divide-white/5 overflow-hidden">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                  <Skeleton className="h-5 w-5 rounded-full shrink-0" />
                  <Skeleton className="h-9 w-9 rounded-lg shrink-0" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-3.5 w-3/4" />
                    <Skeleton className="h-2.5 w-20" />
                  </div>
                  <Skeleton className="h-5 w-20 rounded-full shrink-0" />
                </div>
              ))}
            </div>
          ) : !hasDocuments ? (
            <EmptyState query={debouncedQuery} onUpload={() => fileRef.current?.click()} />
          ) : (
            <motion.div
              layout
              className="rounded-2xl bg-white/[0.03] ring-1 ring-white/10 border-gradient backdrop-blur divide-y divide-white/5 overflow-hidden"
              style={{ borderRadius: 16 }}
            >
              <AnimatePresence mode="popLayout">
                {docs.map((doc) => (
                  <DocumentCard
                    key={doc.id}
                    doc={doc}
                    selected={selectedIds.has(doc.id)}
                    onSelect={() => toggleSelect(doc.id)}
                    onPreview={() => setPreviewDoc({ id: doc.id, mime: doc.mime })}
                  />
                ))}
              </AnimatePresence>
            </motion.div>
          )}
        </div>

        {/* Floating Action Button (mobile) */}
        {selectedCount > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="fixed bottom-6 left-1/2 -translate-x-1/2 lg:hidden"
          >
            <Button onClick={goToChat} size="lg" className="gap-2 shadow-lg rounded-full px-6">
              <MessageSquare className="h-5 w-5" />
              Talk to Agent ({selectedCount})
            </Button>
          </motion.div>
        )}
      </div>
    </TooltipProvider>
  )
}

// Document Card Component
function DocumentCard({
  doc,
  selected,
  onSelect,
  onPreview,
}: {
  doc: Document
  selected: boolean
  onSelect: () => void
  onPreview: () => void
}) {
  const getIcon = () => {
    if (doc.mime === 'application/pdf') return <FileText className="h-4 w-4" />
    if (doc.mime === 'text/plain') return <Globe className="h-4 w-4" />
    if (doc.mime.startsWith('image/')) return <Image className="h-4 w-4" />
    if (doc.mime.startsWith('audio/')) return <Music className="h-4 w-4" />
    if (doc.mime.startsWith('video/')) return <Video className="h-4 w-4" />
    return <File className="h-4 w-4" />
  }

  const getStatusBadge = () => {
    switch (doc.status) {
      case 'pending':
        return (
          <Badge variant="warning" className="gap-1">
            <Clock className="h-3 w-3" />
            Pending
          </Badge>
        )
      case 'processing':
        return (
          <Badge variant="secondary" className="gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            Processing
          </Badge>
        )
      case 'completed':
        return (
          <Badge variant="success" className="gap-1">
            <CheckCircle2 className="h-3 w-3" />
            Ready
          </Badge>
        )
      case 'failed':
        return (
          <Badge variant="destructive" className="gap-1">
            <AlertCircle className="h-3 w-3" />
            Failed
          </Badge>
        )
    }
  }

  const isReady = doc.status === 'completed'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 8 }}
      transition={{ duration: 0.2 }}
    >
      <div
        className={`group relative flex items-center gap-4 px-4 sm:px-5 py-3.5 cursor-pointer transition-colors ${
          selected ? 'bg-blue-400/10' : 'hover:bg-white/[0.03]'
        } ${!isReady ? 'opacity-60' : ''}`}
        onClick={() => isReady && onSelect()}
      >
        {/* Left edge selection accent */}
        {selected && (
          <motion.span
            initial={{ scaleY: 0 }}
            animate={{ scaleY: 1 }}
            className="absolute left-0 top-2 bottom-2 w-0.5 rounded-r bg-blue-400 origin-center"
          />
        )}

        {/* Selection check / placeholder */}
        <div className="shrink-0 h-5 w-5 flex items-center justify-center">
          {selected ? (
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              className="h-5 w-5 rounded-full bg-blue-400 text-neutral-950 flex items-center justify-center"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
            </motion.span>
          ) : (
            <span className="h-4 w-4 rounded-full ring-1 ring-white/15 group-hover:ring-white/40 transition-colors" />
          )}
        </div>

        {/* Modality icon */}
        <div className="shrink-0 h-9 w-9 rounded-lg bg-white/5 ring-1 ring-white/10 flex items-center justify-center text-blue-300">
          {getIcon()}
        </div>

        {/* Title + meta */}
        <div className="flex-1 min-w-0">
          {doc.summary ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <p className="text-sm font-medium text-white truncate cursor-help">
                  {doc.title}
                </p>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs text-xs">
                {doc.summary}
              </TooltipContent>
            </Tooltip>
          ) : (
            <p className="text-sm font-medium text-white truncate">{doc.title}</p>
          )}
          <p className="text-[10px] font-mono text-neutral-500 mt-0.5 truncate">
            {doc.chunk_count != null && doc.chunk_count > 0
              ? `${doc.chunk_count} chunks`
              : '— · indexing'}
          </p>
        </div>

        {/* Status badge */}
        <div className="shrink-0">
          {getStatusBadge()}
        </div>

        {/* Preview action */}
        {isReady && (
          <Button
            variant="ghost"
            size="sm"
            className="shrink-0 text-xs h-8 px-2.5 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
            onClick={(e) => {
              e.stopPropagation()
              onPreview()
            }}
          >
            Preview
          </Button>
        )}
      </div>
    </motion.div>
  )
}

// Empty State Component
function EmptyState({ query, onUpload }: { query: string; onUpload: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-2xl bg-white/[0.03] ring-1 ring-white/10 border-gradient backdrop-blur p-8 sm:p-12"
      style={{ borderRadius: 16 }}
    >
      <div className="flex items-center gap-3 mb-5">
        <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
          00 / {query ? 'No matches' : 'Cold start'}
        </span>
        <span className="h-px flex-1 max-w-[80px] bg-white/10" />
      </div>

      {query ? (
        <>
          <h2 className="text-2xl sm:text-3xl font-semibold tracking-tighter text-white mb-3">
            Nothing matches that.
          </h2>
          <p className="text-sm text-neutral-400 max-w-md mb-7 leading-relaxed">
            Try a different search term, or drop in something the team is missing.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            <Button onClick={onUpload} className="gap-2">
              <Upload className="h-4 w-4" />
              Upload
            </Button>
            <span className="text-[11px] font-mono text-neutral-500">
              <span className="text-blue-300">$</span> upload --query <span className="text-neutral-300">&quot;{query}&quot;</span>
            </span>
          </div>
        </>
      ) : (
        <>
          <h2 className="text-2xl sm:text-3xl font-semibold tracking-tighter text-white mb-3">
            Drop your team&apos;s
            <br />
            knowledge in.
          </h2>
          <p className="text-sm text-neutral-400 max-w-md mb-7 leading-relaxed">
            PDFs, images, audio, video. libmagic sniffs MIME from bytes; embeddings flow on the way through.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            <Button onClick={onUpload} className="gap-2">
              <Upload className="h-4 w-4" />
              Upload first document
            </Button>
            <span className="text-[11px] font-mono text-neutral-500">
              <span className="text-blue-300">$</span> upload --any <span className="text-neutral-300">pdf|image|audio|video</span>
            </span>
          </div>
        </>
      )}
    </motion.div>
  )
}

// Preview Modal Component
function PreviewModal({
  id,
  mime,
  getToken,
  onClose,
}: {
  id: string
  mime: string
  getToken: () => Promise<string | undefined>
  onClose: () => void
}) {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    (async () => {
      try {
        const token = await getToken()
        const res = await fetch(`/api/document/${id}/preview`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        const data = await res.json()
        setUrl(data.signed_url)
      } finally {
        setLoading(false)
      }
    })()
  }, [id, getToken])

  const renderPreview = () => {
    if (loading) {
      return (
        <div className="flex items-center justify-center h-full">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (mime.startsWith('audio/')) {
      return (
        <div className="flex items-center justify-center h-full">
          <audio controls src={url} className="w-full max-w-md" />
        </div>
      )
    }

    if (mime.startsWith('video/')) {
      return <video controls src={url} className="w-full h-full object-contain" />
    }

    if (mime.startsWith('image/')) {
      return (
        <div className="flex items-center justify-center h-full p-4">
          <img src={url} alt="Preview" className="max-w-full max-h-full object-contain rounded-lg" />
        </div>
      )
    }

    return <iframe src={url} className="w-full h-full border-none" />
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 bg-neutral-950/85 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="relative w-full h-full max-w-6xl max-h-[90vh] bg-neutral-900 ring-1 ring-white/10 rounded-[2rem] overflow-hidden shadow-2xl shadow-black/60"
        onClick={(e) => e.stopPropagation()}
      >
        <Button
          variant="secondary"
          size="icon"
          className="absolute top-4 right-4 z-20 rounded-full shadow-lg"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
        {renderPreview()}
      </motion.div>
    </motion.div>
  )
}
