'use client'

/**
 * O idioma da landing, como ilha de cliente. O resto da pagina e renderizado
 * no servidor; so os textos traduzidos e o botao EN|PT dependem do navegador.
 * O servidor entrega em ingles (o estado inicial do hook), entao o HTML que um
 * crawler recebe ja tem o conteudo inteiro.
 */

import { createContext, useContext, useEffect, type ReactNode } from 'react'
import { useLocale } from '@/hooks/useLocale'
import type { Locale, TranslationKey } from '@/lib/i18n'

type LocaleContextValue = {
  locale: Locale
  toggleLocale: () => void
  t: (key: TranslationKey) => string
}

const LocaleContext = createContext<LocaleContextValue | null>(null)

export function LandingLocaleProvider({ children }: { children: ReactNode }) {
  const { locale, toggleLocale, t } = useLocale()

  // Leitor de tela e tradutor do navegador leem o `lang` do documento.
  useEffect(() => {
    document.documentElement.lang = locale === 'pt' ? 'pt-BR' : 'en'
    return () => {
      document.documentElement.lang = 'en'
    }
  }, [locale])

  return (
    <LocaleContext.Provider value={{ locale, toggleLocale, t }}>{children}</LocaleContext.Provider>
  )
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

export function LocaleToggle() {
  const { locale, toggleLocale } = useLandingLocale()
  return (
    <button
      onClick={toggleLocale}
      className="flex items-center gap-1 rounded-md border border-white/10 px-2.5 py-1 text-xs font-medium text-neutral-300 transition-colors hover:bg-white/5"
      aria-label={locale === 'en' ? 'Mudar para português' : 'Switch to English'}
    >
      <span className={locale === 'en' ? 'font-bold text-white' : ''}>EN</span>
      <span className="text-neutral-600">|</span>
      <span className={locale === 'pt' ? 'font-bold text-white' : ''}>PT</span>
    </button>
  )
}
