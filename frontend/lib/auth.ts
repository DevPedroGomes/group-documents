const TOKEN_KEY = 'brainhub_token';
const USER_KEY = 'brainhub_user';

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
}

/** O usuario como /auth/login, /auth/register, /auth/guest e /auth/me devolvem. */
export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_active?: boolean;
  created_at?: string;
  /** Sessao de convidado: le o acervo de demonstracao, so leitura. */
  is_guest?: boolean;
  /** Fim da sessao de convidado, ISO. Nulo para conta normal. */
  expires_at?: string | null;
  /** Perguntas que restam na sessao de convidado; o `done` do chat atualiza. */
  questions_left?: number | null;
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

/** Dica dos controles que gravam (upload, URL, voz), desabilitados para convidado. */
export const GUEST_READ_ONLY_TITLE = 'Create an account to add your own files';

/** Convidado: o acervo e o de demonstracao e nada pode ser gravado. */
export function isGuest(user: User | null | undefined): boolean {
  return user?.is_guest === true;
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

// Aviso de sessao de convidado vencida, lido uma vez pela landing. Fica no
// sessionStorage: some com a aba e nao vaza para a proxima visita.
const GUEST_ENDED_KEY = 'brainhub_guest_session_ended';

export function markGuestSessionEnded() {
  try {
    sessionStorage.setItem(GUEST_ENDED_KEY, '1');
  } catch {}
}

/** True uma vez depois que a sessao de convidado venceu; le e apaga. */
export function takeGuestSessionEnded(): boolean {
  try {
    const encerrada = sessionStorage.getItem(GUEST_ENDED_KEY) === '1';
    sessionStorage.removeItem(GUEST_ENDED_KEY);
    return encerrada;
  } catch {
    return false;
  }
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
