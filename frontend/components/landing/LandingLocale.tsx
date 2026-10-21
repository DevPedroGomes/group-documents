'use client'

/**
 * Os textos da landing, como ilha de cliente. O resto da pagina e renderizado
 * no servidor. O idioma e fixo em ingles (ver hooks/useLocale.ts); o
 * dicionario continua passando por aqui para uma traducao futura nao precisar
 * mexer em cada componente. O HTML que um crawler recebe ja tem o conteudo
 * inteiro.
 */

import { createContext, useContext, type ReactNode } from 'react'
import { useLocale } from '@/hooks/useLocale'
import type { Locale, TranslationKey } from '@/lib/i18n'

type LocaleContextValue = {
  locale: Locale
  t: (key: TranslationKey) => string
}

const LocaleContext = createContext<LocaleContextValue | null>(null)

export function LandingLocaleProvider({ children }: { children: ReactNode }) {
  const { locale, t } = useLocale()
  return <LocaleContext.Provider value={{ locale, t }}>{children}</LocaleContext.Provider>
}

function useLandingLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext)
  if (!ctx) throw new Error('useLandingLocale must be used within LandingLocaleProvider')
  return ctx
}

/** Um texto traduzido, dentro de um componente de servidor. */
export function T({ k }: { k: TranslationKey }) {
  const { t } = useLandingLocale()
  return <>{t(k)}</>
}
