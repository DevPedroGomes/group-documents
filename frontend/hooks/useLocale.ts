'use client'

import { useCallback, useEffect, useState } from 'react'
import { getTranslation, type Locale } from '@/lib/i18n'

const STORAGE_KEY = 'group-docs-locale'

function detectInitial(): Locale {
  if (typeof window === 'undefined') return 'en'
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'en' || stored === 'pt') return stored
  } catch {}
  if (typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('pt')) {
    return 'pt'
  }
  return 'en'
}

export function useLocale() {
  const [locale, setLocaleState] = useState<Locale>('en')

  useEffect(() => {
    setLocaleState(detectInitial())
  }, [])

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {}
  }, [])

  const toggleLocale = useCallback(() => {
    setLocale(locale === 'en' ? 'pt' : 'en')
  }, [locale, setLocale])

  const t = getTranslation(locale)
  return { locale, setLocale, toggleLocale, t }
}
