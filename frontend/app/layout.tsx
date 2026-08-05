import '@/styles/globals.css'
import type { Metadata } from 'next'
import { AuthProvider } from '@/contexts/AuthContext'

export const metadata: Metadata = {
  metadataBase: new URL("https://group-documents.pgdev.com.br"),
  title: "BrainHub — multimodal RAG that grades its own retrieval",
  description:
    "Upload documents and ask. A LangGraph corrective-RAG pipeline retrieves, reranks, grades what it found, rewrites the query and falls back to web search when the answer is not in your files.",
  authors: [{ name: "Pedro Gomes", url: "https://gomio.com.br" }],
  creator: "Pedro Gomes",
  // Sem openGraph/twitter o link vira URL crua no LinkedIn, no WhatsApp e no
  // Upwork — que e onde estas demos de facto circulam.
  openGraph: {
    type: "website",
    url: "https://group-documents.pgdev.com.br",
    siteName: "Gomio",
    locale: "en_US",
    title: "BrainHub — multimodal RAG that grades its own retrieval",
    description:
      "Upload documents and ask. A LangGraph corrective-RAG pipeline retrieves, reranks, grades what it found, rewrites the query and falls back to web search when the answer is not in your files.",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "BrainHub — multimodal RAG over your own documents" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "BrainHub — multimodal RAG that grades its own retrieval",
    description:
      "Upload documents and ask. A LangGraph corrective-RAG pipeline retrieves, reranks, grades what it found, rewrites the query and falls back to web search when the answer is not in your files.",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh bg-neutral-950 text-white font-sans antialiased">
        <div className="app-bg" aria-hidden />
        <div className="app-grid" aria-hidden />
        <AuthProvider>{children}</AuthProvider>
        {/* Sem isto a demo termina em si mesma: quem gostou do que viu nao tem
            para onde ir. Era a unica do portfolio sem nenhuma atribuicao. */}
        <footer className="px-6 py-5 text-center text-xs text-white/35">
          Built by{' '}
          <a
            href="https://gomio.com.br"
            target="_blank"
            rel="noopener"
            className="text-white/55 underline decoration-white/20 underline-offset-2 hover:text-white"
          >
            Pedro Gomes
          </a>{' '}
          ·{' '}
          <a
            href="https://portfolio.pgdev.com.br"
            target="_blank"
            rel="noopener"
            className="text-white/55 underline decoration-white/20 underline-offset-2 hover:text-white"
          >
            other projects
          </a>
        </footer>
      </body>
    </html>
  )
}
