# 🚀 Hub Agno + Supabase - Document Q&A with RAG

Sistema completo de gerenciamento e consulta de documentos usando RAG (Retrieval-Augmented Generation) com **Agno Agent Framework**, **Supabase** e **Next.js 14**.

---

## 📋 Stack Tecnológica

### Backend

- **FastAPI** - API REST
- **Agno 2.2.13** - Framework de agents com tools customizados
- **OpenAI GPT-4o-mini** - Modelo de linguagem
- **Supabase (PostgreSQL + pgvector)** - Database e vector search
- **SQLAlchemy** - ORM
- **Langfuse** (opcional) - Observabilidade

### Frontend

- **Next.js 14** (App Router)
- **React 18**
- **Supabase Auth + Storage**
- **Tailwind CSS**
- **TypeScript**

---

## ✨ Funcionalidades

- ✅ **Upload de PDFs** no Supabase Storage
- ✅ **Ingestão automática** - Extração de texto, chunking e geração de embeddings
- ✅ **Busca semântica** com pgvector (cosine similarity)
- ✅ **Chat RAG inteligente** usando Agno Agent Framework
- ✅ **Citações automáticas** - O agente sempre cita documento e página
- ✅ **Autenticação** com Supabase Auth
- ✅ **RLS (Row Level Security)** - Isolamento total entre usuários
- ✅ **Preview de documentos** com signed URLs
- ✅ **Error handling robusto** - Validações e mensagens claras

---

## 🚀 Setup Rápido

### 1. Pré-requisitos

- Node.js 18+
- Python 3.12+
- Conta no Supabase
- OpenAI API Key

### 2. Configurar Supabase

**Siga o guia completo:** [`SUPABASE_SETUP.md`](./SUPABASE_SETUP.md)

Resumo:

1. Criar projeto no Supabase
2. Executar SQL completo em `sql/schema_complete.sql`
3. Criar bucket `docs` no Storage
4. Configurar policies de RLS

### 3. Configurar Variáveis de Ambiente

#### Backend (`backend/.env`)

```bash
# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
SUPABASE_JWT_SECRET=your-jwt-secret
SUPABASE_DB_URL=postgresql://postgres:password@db.xxx.supabase.co:5432/postgres

# Storage
STORAGE_BUCKET=docs

# OpenAI
OPENAI_API_KEY=sk-proj-...
MODEL=gpt-4o-mini
EMB_MODEL=text-embedding-3-small
SIM_THRESHOLD=0.2

# CORS
CORS_ORIGINS=http://localhost:3000

# Langfuse (opcional)
LANGFUSE_ENABLED=false
```

#### Frontend (`frontend/.env.local`)

```bash
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 4. Instalar Dependências

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd frontend
npm install
```

### 5. Rodar o Projeto

#### Opção 1: Rodar tudo junto (recomendado)

```bash
# Na raiz do projeto
npm run dev
```

Isso inicia:

- Backend: http://localhost:8000
- Frontend: http://localhost:3000

#### Opção 2: Rodar separadamente

Terminal 1 (Backend):

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Terminal 2 (Frontend):

```bash
cd frontend
npm run dev
```

---

## 📖 Como Usar

### 1. Criar Conta

1. Acesse http://localhost:3000
2. Clique em "Sign Up"
3. Crie sua conta com email e senha

### 2. Fazer Upload de PDF

1. Faça login
2. Clique em "Enviar Documento"
3. Selecione um PDF (máximo 10MB)
4. Aguarde o processamento (extração + embeddings)

### 3. Fazer Perguntas

1. Digite sua pergunta no chat
2. O agente Agno buscará semanticamente nos seus documentos
3. Receberá resposta com citações (documento + página)

---

## 🔧 Arquivos Importantes

### Backend

| Arquivo                     | Descrição                                        |
| --------------------------- | ------------------------------------------------ |
| `backend/app/main.py`       | API FastAPI com endpoints e CORS configurado     |
| `backend/app/agno_agent.py` | **Agent Agno com custom tools** para RAG         |
| `backend/app/rag.py`        | Geração de embeddings (OpenAI)                   |
| `backend/app/ingest.py`     | Extração de texto de PDF e chunking              |
| `backend/app/auth.py`       | Validação de JWT do Supabase                     |
| `backend/app/models.py`     | Models SQLAlchemy (documents, chunks)            |
| `backend/requirements.txt`  | Dependências Python (incluindo **agno==2.2.13**) |

### Frontend

| Arquivo                                | Descrição                    |
| -------------------------------------- | ---------------------------- |
| `frontend/app/page.tsx`                | Página principal (Login/Hub) |
| `frontend/app/layout.tsx`              | Layout raiz com metadata     |
| `frontend/components/KnowledgeHub.tsx` | UI principal (upload + chat) |

### SQL & Docs

| Arquivo                   | Descrição                                                            |
| ------------------------- | -------------------------------------------------------------------- |
| `sql/schema_complete.sql` | **Schema SQL completo** (tabelas, índices, RLS, triggers, functions) |
| `SUPABASE_SETUP.md`       | **Guia passo a passo** para configurar Supabase manualmente          |

---

## 🎯 Principais Melhorias Implementadas

### ✅ Fase 1: Correções Críticas

- Corrigido import do CSS no layout.tsx
- Virtual environment movido para local correto (`backend/.venv/`)
- Arquivos `.env` criados com comentários explicativos
- `.gitignore` adicionados (root, frontend, backend)

### ✅ Fase 2: Configuração do Supabase

- Schema SQL completo com:
  - Tabelas: `documents`, `chunks`, `threads`, `messages`
  - Índices para performance (including IVFFlat para vector search)
  - RLS policies para isolamento multi-tenant
  - Triggers para atualização automática de timestamps
  - Functions RPC para busca semântica otimizada
- Guia de setup manual detalhado (`SUPABASE_SETUP.md`)
- Instruções para configurar Storage bucket `docs` com policies

### ✅ Fase 3: Integração Real do Agno

- **Reescrita completa** do `agno_agent.py` usando Agno Framework
- Custom tools implementadas:
  - `search_tool` - Busca semântica com pgvector
  - `list_tool` - Listar documentos do usuário
- Agent configurado com:
  - Model: GPT-4o-mini
  - Instructions claras para citar fontes
  - Markdown habilitado
  - Show tool calls desabilitado (UX melhor)
- Mantida compatibilidade com interface existente

### ✅ Fase 4: Segurança e Qualidade

- **CORS configurado corretamente** via variável `CORS_ORIGINS`
- **Validação de inputs** em todos os endpoints:
  - Tamanho máximo de arquivo (10MB)
  - Tipo MIME (apenas PDF)
  - Tamanho de mensagens
  - Títulos de documentos
- **Error handling robusto**:
  - Try/catch em todos os endpoints
  - Mensagens de erro claras
  - Rollback automático em falhas (delete de documento se falhar embeddings)
  - Logging estruturado
- **Transações atômicas** garantindo consistência do banco

---

## 🐛 Troubleshooting

### Backend não inicia

**Erro:** `ModuleNotFoundError: No module named 'agno'`

**Solução:**

```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
```

### Frontend não compila

**Erro:** `Module not found: Can't resolve './globals.css'`

**Solução:** Já corrigido! Mas se ocorrer, verifique se o import em `layout.tsx` é `import '../styles/globals.css'`

### Chat não responde

**Possíveis causas:**

1. **Nenhum documento enviado**
   - Solução: Faça upload de PDFs primeiro

2. **OpenAI API Key inválida**
   - Solução: Verifique `OPENAI_API_KEY` em `backend/.env`

3. **Supabase não configurado**
   - Solução: Siga o guia `SUPABASE_SETUP.md`

### Upload falha com 403 Forbidden

**Causa:** Storage policies não configuradas

**Solução:** Configure as 3 policies do bucket `docs` (INSERT, SELECT, DELETE) conforme `SUPABASE_SETUP.md`

---

## 📊 Estrutura do Banco de Dados

```sql
documents
├── id (UUID, PK)
├── user_id (UUID, FK auth.users)
├── title (TEXT)
├── mime (TEXT)
├── storage_path (TEXT)
├── meta (JSONB)
└── uploaded_at (TIMESTAMPTZ)

chunks
├── id (UUID, PK)
├── user_id (UUID)
├── document_id (UUID, FK documents)
├── page (INTEGER)
├── chunk_index (INTEGER)
├── text (TEXT)
└── embedding (VECTOR(1536))  -- OpenAI text-embedding-3-small

threads (para futuro uso)
├── id (UUID, PK)
├── user_id (UUID)
├── title (TEXT)
├── created_at (TIMESTAMPTZ)
└── updated_at (TIMESTAMPTZ)

messages (para futuro uso)
├── id (UUID, PK)
├── thread_id (UUID, FK threads)
├── role (TEXT: 'user'|'assistant'|'system')
├── content (TEXT)
├── meta (JSONB)
└── created_at (TIMESTAMPTZ)
```

---

## 🔮 Próximos Passos (Roadmap)

### Thread Persistence (P1)

- [ ] Implementar criação de threads no backend
- [ ] Salvar histórico de mensagens
- [ ] UI para listar e retomar conversas
- [ ] Integrar com Agno Sessions

### UX Melhorado (P2)

- [ ] Loading states com skeleton loaders
- [ ] Streaming de respostas do chat
- [ ] Progress bar em uploads
- [ ] Error boundaries no frontend
- [ ] Toast notifications

### Features Adicionais (P3)

- [ ] Suporte para mais formatos (DOCX, TXT, MD)
- [ ] Busca full-text além de semântica
- [ ] Highlight de trechos citados
- [ ] Exportar conversas
- [ ] Estatísticas de uso

### DevOps (P4)

- [ ] Docker compose para desenvolvimento
- [ ] CI/CD com GitHub Actions
- [ ] Deploy no Vercel (frontend)
- [ ] Deploy no Railway/Render (backend)
- [ ] Testes unitários e E2E

---

## 🤝 Contribuindo

Este é um projeto educacional/pessoal. Sinta-se livre para:

- Abrir issues para reportar bugs
- Sugerir melhorias
- Fazer fork e experimentar

---

## 📄 Licença

MIT License - Use à vontade!

---

## 🙏 Agradecimentos

- **Agno** - Framework de agents incrivelmente poderoso
- **Supabase** - Backend as a Service completo e open source
- **OpenAI** - Modelos de linguagem e embeddings
- **Next.js** - Framework React excepcional

---

## 📞 Suporte

Para problemas relacionados ao projeto:

- Consulte `SUPABASE_SETUP.md` para setup do Supabase
- Verifique a seção Troubleshooting neste README
- Revise os logs do backend e frontend

Para problemas com tecnologias específicas:

- Supabase: https://supabase.com/docs
- Agno: https://docs.agno.com
- Next.js: https://nextjs.org/docs

---

**Feito com ❤️ usando Agno, Supabase e Next.js**
