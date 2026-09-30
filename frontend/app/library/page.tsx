'use client'

import { useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { FileStack } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import Topbar from '@/components/Topbar'
import KnowledgeHub from '@/components/KnowledgeHub'

// A biblioteca era renderizada dentro da landing, em `/`, depois de checar a
// sessao no navegador: o HTML de `/` saia vazio para todo mundo. Agora `/` e
// estatica e a area logada tem rota propria.
export default function LibraryPage() {
  const router = useRouter()
  const { user, loading, logout, getToken } = useAuth()

  // Estavel: o KnowledgeHub busca a lista num efeito que depende dela, e uma
  // funcao nova a cada render buscava a lista de novo a cada render.
  const getTokenAsync = useCallback(async () => getToken() ?? undefined, [getToken])

  useEffect(() => {
    if (!loading && !user) router.replace('/')
  }, [loading, user, router])

  if (loading || !user) {
    return (
      <main className="min-h-dvh flex items-center justify-center px-6">
        <div className="flex flex-col items-center gap-6 fade-slide-in">
          <div className="flex items-center gap-3">
            <div
              className="relative flex h-10 w-10 items-center justify-center rounded-full bg-white/10 ring-1 ring-white/15 backdrop-blur"
              style={{ borderRadius: 9999 }}
            >
              <FileStack className="h-4 w-4 text-white" />
              <span
                className="absolute inset-0 rounded-full ring-2 ring-blue-400/40 animate-ping"
                style={{ borderRadius: 9999 }}
                aria-hidden
              />
            </div>
            <span className="font-semibold tracking-tight text-white text-lg">BrainHub</span>
          </div>
          <div className="flex items-center gap-3 text-[11px] font-mono text-neutral-400" role="status">
            <span className="text-blue-300">$</span>
            <span>checking session</span>
            <span className="ml-1 inline-block w-1.5 h-3 bg-blue-300/80 animate-pulse" aria-hidden />
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="h-dvh flex flex-col">
      <div className="flex flex-col h-full xl:max-w-[1400px] xl:mx-auto xl:my-4 glass-panel xl:rounded-[2rem] xl:ring-1 xl:ring-white/10 xl:shadow-2xl xl:shadow-black/40 overflow-hidden">
        <Topbar email={user.email} onSignOut={logout} />
        <KnowledgeHub getToken={getTokenAsync} />
      </div>
    </main>
  )
}
