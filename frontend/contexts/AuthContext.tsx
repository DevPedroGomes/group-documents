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
  isUser,
  type User,
} from '@/lib/auth'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

interface AuthContextType {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName: string) => Promise<void>
  logout: () => void
  getToken: () => string | null
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

/** Corpo de /auth/login e /auth/register; resposta fora do formato e erro, nao sessao pela metade. */
async function lerSessao(res: Response): Promise<{ access_token: string; user: User }> {
  const data = (await res.json().catch(() => null)) as { access_token?: unknown; user?: unknown } | null
  if (!data || typeof data.access_token !== 'string' || !isUser(data.user)) {
    throw new Error('Unexpected response from the server')
  }
  return { access_token: data.access_token, user: data.user }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

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
        if (isUser(data)) {
          setUser(data)
          // Update stored user with fresh data
          setAuth(token, data)
        } else {
          // Token is invalid, clear it
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

  const logout = useCallback(() => {
    clearAuth()
    setUser(null)
  }, [])

  const getToken = useCallback(() => {
    return getStoredToken()
  }, [])

  return (
    <AuthContext.Provider
      value={{ user, loading, login, register, logout, getToken }}
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
