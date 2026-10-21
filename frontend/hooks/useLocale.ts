import { getTranslation, type Locale } from '@/lib/i18n'

// A landing sai so em ingles. O portugues traduzia o hero e mais nada: quem
// chegava com o navegador em pt-BR lia o titulo em portugues e o resto da
// pagina, o formulario e o app inteiro em ingles. O dicionario de lib/i18n.ts
// fica para uma traducao completa, se ela vier.
const LOCALE: Locale = 'en'

export function useLocale() {
  return { locale: LOCALE, t: getTranslation(LOCALE) }
}
