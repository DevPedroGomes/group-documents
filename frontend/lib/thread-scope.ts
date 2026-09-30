/**
 * Escopo de documentos de cada conversa, guardado no navegador.
 *
 * O backend nao grava que documentos uma thread usou, entao reabrir uma
 * conversa antiga caia em `/chat` sem `?docs=` e o campo ficava desabilitado.
 * Aqui fica o ultimo escopo de cada thread aberta neste navegador; sem registro
 * (outro navegador, dados limpos), a conversa segue no acervo inteiro.
 */

const CHAVE = 'brainhub_thread_scope'
const MAXIMO = 50

type Registro = [threadId: string, documentIds: string[]]

function ler(): Registro[] {
  try {
    const bruto = localStorage.getItem(CHAVE)
    const lista: unknown = bruto ? JSON.parse(bruto) : []
    if (!Array.isArray(lista)) return []
    return lista.filter(
      (r): r is Registro =>
        Array.isArray(r) &&
        typeof r[0] === 'string' &&
        Array.isArray(r[1]) &&
        r[1].every((id: unknown) => typeof id === 'string')
    )
  } catch {
    return []
  }
}

export function salvarEscopo(threadId: string, documentIds: string[]): void {
  try {
    const resto = ler().filter(([id]) => id !== threadId)
    localStorage.setItem(CHAVE, JSON.stringify([[threadId, documentIds], ...resto].slice(0, MAXIMO)))
  } catch {
    // Armazenamento bloqueado: a conversa so nao lembra o escopo.
  }
}

/** Os ids da ultima pergunta da thread; `null` quando este navegador nao sabe. */
export function lerEscopo(threadId: string): string[] | null {
  const achado = ler().find(([id]) => id === threadId)
  return achado ? achado[1] : null
}
