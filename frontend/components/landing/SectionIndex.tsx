'use client'

/**
 * Indice lateral da landing, com a secao ativa de verdade.
 *
 * Antes o rotulo so aparecia no hover: com o cursor no meio da tela, "PIPELINE"
 * acendia sobre qualquer secao. Agora um IntersectionObserver olha uma faixa
 * fina no meio da viewport (45% a 50% da altura) e a secao que cruza essa faixa
 * e a ativa. Com o formulario de acesso na faixa, nenhum item fica ativo: ele
 * nao esta no indice.
 *
 * So aparece a partir de 1700 px, com margem para os rotulos ao lado do
 * container principal sem cobrir os titulos.
 */

import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { T } from '@/components/landing/LandingLocale'

export const SECTION_IDS = ['index', 'different', 'pipeline', 'features', 'stack'] as const

type SectionId = (typeof SECTION_IDS)[number]

const FORMULARIO = 'auth'

export default function SectionIndex() {
  const [ativa, setAtiva] = useState<SectionId | null>(null)

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return

    const naFaixa = new Set<string>()
    const observador = new IntersectionObserver(
      (entradas) => {
        for (const entrada of entradas) {
          if (entrada.isIntersecting) naFaixa.add(entrada.target.id)
          else naFaixa.delete(entrada.target.id)
        }
        if (naFaixa.has(FORMULARIO)) {
          setAtiva(null)
          return
        }
        // Entre duas secoes (a faixa de numeros, por exemplo) nada cruza a
        // faixa: o item ativo continua o da ultima secao vista.
        const atual = SECTION_IDS.find((id) => naFaixa.has(id))
        if (atual) setAtiva(atual)
      },
      { rootMargin: '-45% 0px -50% 0px' }
    )

    for (const id of [...SECTION_IDS, FORMULARIO]) {
      const alvo = document.getElementById(id)
      if (alvo) observador.observe(alvo)
    }
    return () => observador.disconnect()
  }, [])

  return (
    <nav
      className="hidden min-[1700px]:flex flex-col gap-3 fixed left-4 top-1/2 -translate-y-1/2 z-30"
      aria-label="Section index"
    >
      {SECTION_IDS.map((id, i) => {
        const ehAtiva = ativa === id
        return (
          <a
            key={id}
            href={`#${id}`}
            aria-current={ehAtiva ? 'true' : undefined}
            className={cn(
              'group flex items-center gap-2 text-[10px] font-mono uppercase tracking-wider transition-colors',
              ehAtiva ? 'text-white' : 'text-neutral-400 hover:text-white focus-visible:text-white'
            )}
          >
            <span className="w-5 text-right">{`0${i + 1}`}</span>
            <span
              aria-hidden
              className={cn(
                'h-px transition-all',
                ehAtiva ? 'w-6 bg-blue-300' : 'w-3 bg-white/10 group-hover:w-6 group-hover:bg-blue-300'
              )}
            />
            <span
              className={cn(
                'max-w-[4.5rem] leading-tight transition-opacity',
                ehAtiva ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100'
              )}
            >
              <T k={`sections.${id}`} />
            </span>
          </a>
        )
      })}
    </nav>
  )
}
