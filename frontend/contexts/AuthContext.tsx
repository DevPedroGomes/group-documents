'use client'

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  ReactNode,
} from 'react'
import {
  getToken as getStoredToken,
  getUser as getStoredUser,
  setAuth,
  clearAuth,
  isGuest,
  isUser,
  markGuestSessionEnded,
  type User,
} from '@/lib/auth'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/** `name` do erro de `loginAsGuest` quando o modo convidado esta desligado. */
export const GUEST_UNAVAILABLE = 'GuestUnavailableError'

interface AuthContextType {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName: string) => Promise<void>
  /** Abre uma sessao de convidado: acervo de demonstracao, so leitura. */
  loginAsGuest: () => Promise<void>
  logout: () => void
  /** Encerra a sessao de convidado vencida e deixa o aviso para a landing. */
  endGuestSession: () => void
  /** Atualiza as perguntas que restam ao convidado (vem no `done` do chat). */
  setQuestionsLeft: (restantes: number) => void
  getToken: () => string | null
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

/** Corpo de /auth/login, /auth/register e /auth/guest; resposta fora do formato e erro, nao sessao pela metade. */
async function lerSessao(res: Response): Promise<{ access_token: string; user: User }> {
  const data = (await res.json().catch(() => null)) as { access_token?: unknown; user?: unknown } | null
  if (!data || typeof data.access_token !== 'string' || !isUser(data.user)) {
    throw new Error('Unexpected response from the server')
  }
  return { access_token: data.access_token, user: data.user }
}

function erroDeConvidado(mensagem: string, nome?: string): Error {
  const erro = new Error(mensagem)
  if (nome) erro.name = nome
  return erro
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const endGuestSession = useCallback(() => {
    clearAuth()
    markGuestSessionEnded()
    setUser(null)
  }, [])

  // On mount, check for existing token and validate it
  useEffect(() => {
    const token = getStoredToken()
    if (!token) {
      setLoading(false)
      return
    }

    // Validate token by calling /auth/me
    fetch(`${API_URL}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (res) => {
        const data: unknown = res.ok ? await res.json().catch(() => null) : null
        const guardado = getStoredUser()
        if (isUser(data)) {
          // /auth/me nao sabe quantas perguntas restam ao convidado: o numero
          // guardado continua ate o proximo `done` do chat.
          const atual: User = { ...data, questions_left: data.questions_left ?? guardado?.questions_left ?? null }
          setUser(atual)
          setAuth(token, atual)
        } else {
          // Token is invalid, clear it
          if (res.status === 401 && isGuest(guardado)) markGuestSessionEnded()
          clearAuth()
          setUser(null)
        }
      })
      .catch(() => {
        // Network error - keep existing user from localStorage as fallback
        const storedUser = getStoredUser()
        if (storedUser) {
          setUser(storedUser)
        } else {
          clearAuth()
        }
      })
      .finally(() => setLoading(false))
  }, [])

  // A sessao de convidado vence no horario que o backend deu; o token vence
  // junto. Sem este timer a pessoa so descobriria no proximo 401.
  const fimDoConvidado = isGuest(user) ? user?.expires_at : null
  useEffect(() => {
    if (!fimDoConvidado) return
    const restante = new Date(fimDoConvidado).getTime() - Date.now()
    if (Number.isNaN(restante)) return
    const timer = window.setTimeout(endGuestSession, Math.min(Math.max(restante, 0), 2_147_483_647))
    return () => window.clearTimeout(timer)
  }, [fimDoConvidado, endGuestSession])

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })

    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || 'Invalid email or password')
    }

    const { access_token, user: userData } = await lerSessao(res)
    setAuth(access_token, userData)
    setUser(userData)
  }, [])

  const register = useCallback(
    async (email: string, password: string, fullName: string) => {
      const res = await fetch(`${API_URL}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, full_name: fullName }),
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Registration failed')
      }

      const { access_token, user: userData } = await lerSessao(res)
      setAuth(access_token, userData)
      setUser(userData)
    },
    []
  )

  const loginAsGuest = useCallback(async () => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 20_000)
    let res: Response
    try {
      res = await fetch(`${API_URL}/auth/guest`, { method: 'POST', signal: controller.signal })
    } finally {
      clearTimeout(timeout)
    }

    // 503: modo desligado ou acervo ausente. 404: backend sem a rota.
    // A entrada da demo apresenta a indisponibilidade e o acesso por conta.
    if (res.status === 503 || res.status === 404) {
      throw erroDeConvidado(
        'The sample library is not available right now. Create an account to try BrainHub.',
        GUEST_UNAVAILABLE
      )
    }
    if (res.status === 429) {
      throw erroDeConvidado('Too many sample sessions from this network today. Create an account to keep going.')
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw erroDeConvidado(typeof data.detail === 'string' ? data.detail : 'Could not open the sample library')
    }

    const { access_token, user: userData } = await lerSessao(res)
    setAuth(access_token, userData)
    setUser(userData)
  }, [])

  const setQuestionsLeft = useCallback((restantes: number) => {
    setUser((atual) => {
      if (!atual) return atual
      const proximo = { ...atual, questions_left: restantes }
      const token = getStoredToken()
      if (token) setAuth(token, proximo)
      return proximo
    })
  }, [])

  const logout = useCallback(() => {
    clearAuth()
    setUser(null)
  }, [])

  const getToken = useCallback(() => {
    return getStoredToken()
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        register,
        loginAsGuest,
        logout,
        endGuestSession,
        setQuestionsLeft,
        getToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
