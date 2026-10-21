'use client'

/**
 * Faixa da sessao de convidado, na biblioteca e no chat: o acervo e o de
 * demonstracao, nada pode ser gravado, quando a sessao acaba e quantas
 * perguntas restam. Sem ela o visitante confundiria o botao de upload
 * desabilitado com um app quebrado.
 */

import { Eye } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { isGuest } from '@/lib/auth'

function horaLocal(iso: string | null | undefined): string | null {
  if (!iso) return null
  const data = new Date(iso)
  if (Number.isNaN(data.getTime())) return null
  return data.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function GuestBanner() {
  const { user, logout } = useAuth()
  if (!isGuest(user)) return null

  const fim = horaLocal(user?.expires_at)
  const restantes = user?.questions_left

  // Navegacao do documento, e nao do router: a pagina atual tambem redireciona
  // para `/` quando a sessao some, e o hash do formulario se perderia.
  const criarConta = () => {
    logout()
    window.location.assign('/#auth')
  }

  return (
    <div
      data-guest-banner
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-blue-400/20 bg-blue-400/[0.07] px-4 py-2 text-xs text-blue-100 lg:px-6"
    >
      <Eye className="h-3.5 w-3.5 shrink-0 text-blue-300" aria-hidden />
      <span>
        Sample library, read-only.
        {fim && <> Session ends at {fim}.</>}
        {typeof restantes === 'number' && (
          <> {restantes} {restantes === 1 ? 'question' : 'questions'} left.</>
        )}
      </span>
      <button
        type="button"
        onClick={criarConta}
        className="ml-auto font-medium text-blue-200 underline decoration-blue-300/30 underline-offset-2 hover:text-white"
      >
        Create an account
      </button>
    </div>
  )
}
