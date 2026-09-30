const TOKEN_KEY = 'brainhub_token';
const USER_KEY = 'brainhub_user';

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
}

/** O usuario como /auth/login, /auth/register e /auth/me devolvem. */
export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_active?: boolean;
  created_at?: string;
}

/** O localStorage e a rede devolvem `unknown`: so vira `User` com a forma certa. */
export function isUser(valor: unknown): valor is User {
  if (typeof valor !== 'object' || valor === null) return false;
  const u = valor as Record<string, unknown>;
  return (
    typeof u.id === 'string' &&
    typeof u.email === 'string' &&
    (u.full_name === null || u.full_name === undefined || typeof u.full_name === 'string')
  );
}

export function getUser(): User | null {
  if (typeof window === 'undefined') return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    const valor: unknown = JSON.parse(raw);
    return isUser(valor) ? valor : null;
  } catch {
    return null;
  }
}

export function setAuth(token: string, user: User) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export async function fetchWithAuth(url: string, options: RequestInit = {}) {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return fetch(url, { ...options, headers });
}
