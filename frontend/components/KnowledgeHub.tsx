'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'
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
  CheckCircle2,
  Clock,
  Loader2,
  AlertCircle,
  Sparkles,
} from 'lucide-react'

const supabase = createClient()

interface Document {
  id: string
  title: string
  mime: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
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
  useEffect(() => {
    const hasPending = docs.some(d => d.status === 'pending' || d.status === 'processing')
    if (!hasPending) return

    const interval = setInterval(() => fetchDocs(debouncedQuery), 3000)
    return () => clearInterval(interval)
  }, [docs, debouncedQuery, fetchDocs])

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

  // Upload single file
  const uploadSingleFile = async (file: File, user: any, token: string): Promise<Document | null> => {
    try {
      const ext = file.name.split('.').pop()
      const path = `${user.id}/docs/${crypto.randomUUID()}.${ext}`

      const { error: uploadError } = await supabase.storage
        .from('docs')
        .upload(path, file, { contentType: file.type, upsert: false })

      if (uploadError) throw uploadError

      const res = await fetch('/api/ingest', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          storage_path: path,
          title: file.name,
          mime: file.type,
        }),
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Failed to process ${file.name}`)
      }

      const data = await res.json()
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
      const user = (await supabase.auth.getUser()).data.user
      if (!user) throw new Error('Not authenticated')

      const token = await getToken()
      if (!token) throw new Error('No token available')

      // Upload all files in parallel
      const uploadPromises = Array.from(files).map(file =>
        uploadSingleFile(file, user, token)
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

      <div className="flex flex-col h-[calc(100dvh-57px)] p-4 lg:p-6">
        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">Document Hub</h1>
              <p className="text-muted-foreground text-sm">
                Search and select documents to chat with AI
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
                  <Badge variant="secondary" className="ml-1 bg-primary-foreground/20">
                    {selectedCount}
                  </Badge>
                </Button>
              </motion.div>
            )}
          </div>

          {/* Search & Upload */}
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Describe the document you're looking for..."
                className="pl-9 pr-9 h-11"
              />
              {query && (
                <button
                  onClick={() => setQuery('')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
              {isSearching && (
                <div className="absolute right-10 top-1/2 -translate-y-1/2">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
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
              {uploadingCount > 0 ? `Uploading ${uploadingCount}...` : 'Upload'}
            </Button>
          </div>

          {/* Search hint */}
          {debouncedQuery && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"
            >
              <Sparkles className="h-4 w-4 text-primary" />
              <span>
                Showing documents matching: <strong>"{debouncedQuery}"</strong>
              </span>
            </motion.div>
          )}

          {/* Stats bar */}
          {hasDocuments && (
            <div className="flex items-center justify-between mt-4 text-sm text-muted-foreground">
              <span>{docs.length} documents found</span>
              <div className="flex items-center gap-3">
                {selectedCount > 0 && (
                  <button
                    onClick={() => setSelectedIds(new Set())}
                    className="text-xs hover:text-foreground underline"
                  >
                    Clear selection
                  </button>
                )}
                <span>{selectedCount} selected</span>
              </div>
            </div>
          )}
        </div>

        {/* Error */}
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-4 p-3 rounded-lg bg-destructive/10 text-destructive text-sm flex items-center gap-2"
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

        {/* Documents Grid */}
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
              {[...Array(8)].map((_, i) => (
                <Skeleton key={i} className="h-40 rounded-xl" />
              ))}
            </div>
          ) : !hasDocuments ? (
            <EmptyState query={debouncedQuery} onUpload={() => fileRef.current?.click()} />
          ) : (
            <motion.div
              layout
              className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4"
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
    if (doc.mime === 'application/pdf') return <FileText className="h-8 w-8" />
    if (doc.mime.startsWith('image/')) return <Image className="h-8 w-8" />
    if (doc.mime.startsWith('audio/')) return <Music className="h-8 w-8" />
    if (doc.mime.startsWith('video/')) return <Video className="h-8 w-8" />
    return <File className="h-8 w-8" />
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
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.2 }}
    >
      <Card
        className={`relative p-4 cursor-pointer transition-all duration-200 hover:shadow-md ${
          selected
            ? 'ring-2 ring-primary bg-primary/5'
            : 'hover:bg-muted/50'
        } ${!isReady ? 'opacity-60' : ''}`}
        onClick={() => isReady && onSelect()}
      >
        {/* Selection indicator */}
        {selected && (
          <motion.div
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            className="absolute top-2 left-2 bg-primary text-primary-foreground rounded-full p-1"
          >
            <CheckCircle2 className="h-4 w-4" />
          </motion.div>
        )}

        {/* Status badge */}
        <div className="absolute top-2 right-2">
          {getStatusBadge()}
        </div>

        {/* Content */}
        <div className="flex flex-col items-center justify-center pt-6 pb-2">
          <div className="text-muted-foreground mb-3">
            {getIcon()}
          </div>
          <p className="text-sm font-medium text-center line-clamp-2 break-words w-full">
            {doc.title}
          </p>
        </div>

        {/* Preview button */}
        {isReady && (
          <Button
            variant="ghost"
            size="sm"
            className="w-full mt-2 text-xs"
            onClick={(e) => {
              e.stopPropagation()
              onPreview()
            }}
          >
            Preview
          </Button>
        )}
      </Card>
    </motion.div>
  )
}

// Empty State Component
function EmptyState({ query, onUpload }: { query: string; onUpload: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex flex-col items-center justify-center h-64 border-2 border-dashed border-muted rounded-2xl"
    >
      {query ? (
        <>
          <Search className="h-12 w-12 text-muted-foreground/50 mb-4" />
          <p className="text-lg font-medium text-muted-foreground mb-1">No documents found</p>
          <p className="text-sm text-muted-foreground">
            Try a different search term or upload new documents
          </p>
        </>
      ) : (
        <>
          <Upload className="h-12 w-12 text-muted-foreground/50 mb-4" />
          <p className="text-lg font-medium text-muted-foreground mb-1">No documents yet</p>
          <p className="text-sm text-muted-foreground mb-4">
            Upload your first document to get started
          </p>
          <Button onClick={onUpload} className="gap-2">
            <Upload className="h-4 w-4" />
            Upload Document
          </Button>
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
      className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="relative w-full h-full max-w-6xl max-h-[90vh] bg-background rounded-2xl overflow-hidden shadow-2xl"
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
