import Landing from '@/components/Landing'

export const metadata = {
  title: 'BrainHub — Multi-modal team Q&A with cited answers',
  description:
    'Upload PDFs, images, audio, and video into a shared workspace. Ask anything in natural language, get grounded answers with citations from a Corrective RAG pipeline.',
}

export default function Page() {
  return <Landing />
}
