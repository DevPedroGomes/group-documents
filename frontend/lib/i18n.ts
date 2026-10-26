export type Locale = 'en' | 'pt'

const dict = {
  en: {
    'sections.index': 'Index',
    'sections.different': 'Sources and context',
    'sections.pipeline': 'How it works',
    'sections.features': 'Reliability',
    'sections.stack': 'Architecture',
  },
  pt: {
    'sections.index': 'Início',
    'sections.different': 'Fontes e contexto',
    'sections.pipeline': 'Como funciona',
    'sections.features': 'Confiabilidade',
    'sections.stack': 'Arquitetura',
  },
} as const

export type TranslationKey = keyof typeof dict.en

export function getTranslation(locale: Locale) {
  const table = dict[locale]
  return (key: TranslationKey): string => table[key] ?? key
}
