export type Locale = 'en' | 'pt'

const dict = {
  en: {
    'nav.signIn': 'Sign in',
    'sections.index': 'Index',
    'sections.different': 'Beyond citations',
    'sections.pipeline': 'Pipeline',
    'sections.features': 'Engineering',
    'sections.stack': 'Stack',
    'hero.tag': '01 / Index',
    'hero.version': 'v1.0',
    'hero.title1': 'Everything you know,',
    'hero.title2': 'in one workspace,',
    'hero.title3': 'cited and corrected.',
    'hero.subtitle':
      'Drop PDFs, images, audio and video into one workspace. Ask in plain language: a corrective RAG pipeline retrieves, grades, searches again when nothing fits, and shows the passages behind every answer.',
    'hero.cta.open': 'Open the workspace',
    'hero.cta.read': 'What makes it different',
  },
  pt: {
    'nav.signIn': 'Entrar',
    'sections.index': 'Início',
    'sections.different': 'Além das citações',
    'sections.pipeline': 'Pipeline',
    'sections.features': 'Engenharia',
    'sections.stack': 'Stack',
    'hero.tag': '01 / Início',
    'hero.version': 'v1.0',
    'hero.title1': 'Tudo o que você sabe,',
    'hero.title2': 'em um só espaço,',
    'hero.title3': 'citado e corrigido.',
    'hero.subtitle':
      'Coloque PDFs, imagens, áudio e vídeo em um único espaço. Pergunte em linguagem natural: um pipeline RAG corretivo recupera, avalia, busca de novo quando nada serve e mostra os trechos por trás de cada resposta.',
    'hero.cta.open': 'Abrir workspace',
    'hero.cta.read': 'O que muda aqui',
  },
} as const

export type TranslationKey = keyof typeof dict.en

export function getTranslation(locale: Locale) {
  const table = dict[locale]
  return (key: TranslationKey): string => table[key] ?? key
}
