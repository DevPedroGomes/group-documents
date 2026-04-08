import '@/styles/globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'BrainHub Team',
  description: 'Upload documents and chat with AI using RAG',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh bg-zinc-400/80 font-sans antialiased">
        {children}
      </body>
    </html>
  )
}
