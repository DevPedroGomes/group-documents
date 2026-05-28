export type Locale = 'en' | 'pt'

const dict = {
  en: {
    'nav.signIn': 'Sign in',
    'sections.index': 'Index',
    'sections.pipeline': 'Pipeline',
    'sections.features': 'Engineering',
    'sections.stack': 'Stack',
    'hero.tag': '01 / Index',
    'hero.version': 'v1.0',
    'hero.title1': "Your team's",
    'hero.title2': 'shared brain,',
    'hero.title3': 'cited and corrected.',
    'hero.subtitle':
      'Drop PDFs, images, audio and video into one workspace. Ask anything in plain language — a Corrective RAG pipeline retrieves, grades, rewrites and answers, every claim bound to a source span.',
    'hero.cta.open': 'Open the workspace',
    'hero.cta.read': 'Read the pipeline',
  },
  pt: {
    'nav.signIn': 'Entrar',
    'sections.index': 'Início',
    'sections.pipeline': 'Pipeline',
    'sections.features': 'Engenharia',
    'sections.stack': 'Stack',
    'hero.tag': '01 / Início',
    'hero.version': 'v1.0',
    'hero.title1': 'O cérebro',
    'hero.title2': 'compartilhado do time,',
    'hero.title3': 'citado e corrigido.',
    'hero.subtitle':
      'Coloque PDFs, imagens, áudio e vídeo em um único espaço. Pergunte em linguagem natural — um pipeline RAG corretivo recupera, avalia, reescreve e responde, com cada afirmação ligada a um trecho da fonte.',
    'hero.cta.open': 'Abrir workspace',
    'hero.cta.read': 'Ver o pipeline',
  },
} as const

export type TranslationKey = keyof typeof dict.en

export function getTranslation(locale: Locale) {
  const table = dict[locale]
  return (key: TranslationKey): string => table[key] ?? key
}
