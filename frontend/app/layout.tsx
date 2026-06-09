import '@/styles/globals.css'
import type { Metadata } from 'next'
import { AuthProvider } from '@/contexts/AuthContext'

export const metadata: Metadata = {
  title: 'BrainHub',
  description: 'Upload documents and chat with AI using RAG',
}

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
      </body>
    </html>
  )
}
