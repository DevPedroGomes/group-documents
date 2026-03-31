import os
import re
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import insert, text as sqltext
from .models import documents, chunks, threads, messages
from .auth import require_user
from .observability import start_trace, end_trace
from .supabase_client import create_signed_url
from .ingest import extract_pages_from_pdf, chunk_text
from .rag import embed_texts
from .agno_agent import run_agent, engine
import httpx

# Constantes
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


def validate_storage_path(path: str) -> bool:
    """
    Valida storage_path para evitar path traversal e formatos inválidos.
    Formato esperado: {user_id}/docs/{filename}
    """
    if not path:
        return False
    # Não permite path traversal
    if '..' in path or path.startswith('/'):
        return False
    # Valida formato: uuid/docs/filename.ext
    pattern = r'^[a-f0-9-]+/docs/[^/]+\.(pdf|png|jpg|jpeg|gif|webp|mp3|mp4|wav|webm)$'
    return bool(re.match(pattern, path, re.IGNORECASE))

app = FastAPI(title='Hub Docs + Agente')

# Configuração CORS - usar variável de ambiente
cors_origins = os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in cors_origins],
    allow_credentials=True,
    allow_methods=['GET', 'POST'],
    allow_headers=['Authorization', 'Content-Type']
)

class IngestBody(BaseModel):
    storage_path: str
    title: str
    mime: str

    class Config:
        str_strip_whitespace = True



# Re-implementando ingest para ser clean e chamar função async de verdade
@app.post('/ingest')
async def ingest(request: Request, body: IngestBody, background_tasks: BackgroundTasks):
    """
    Inicia o processamento de um documento PDF em background using FastAPI BackgroundTasks.
    Retorna imediatamente 202 Accepted.
    """
    try:
        user_id = await require_user(request)

        # Validação de input
        if not body.title or len(body.title) > 500:
            raise HTTPException(400, 'Título inválido (máximo 500 caracteres)')
            
        allowed_mimes = ['application/pdf', 'image/jpeg', 'image/png', 'image/webp', 'audio/mpeg', 'audio/wav', 'audio/mp4', 'video/mp4']
        if body.mime not in allowed_mimes and not any(body.mime.startswith(p) for p in ['image/', 'audio/', 'video/']):
            # Permissivo para facilitar
            pass
            
        if not body.storage_path:
            raise HTTPException(400, 'Path do arquivo não fornecido')

        if not validate_storage_path(body.storage_path):
            raise HTTPException(400, 'Caminho de arquivo inválido')

        # Validate that storage path belongs to the authenticated user
        path_user_id = body.storage_path.split('/')[0]
        if path_user_id != user_id:
            raise HTTPException(403, 'Storage path does not belong to this user')

        # Cria o registro "pending" no banco
        try:
            with engine.begin() as conn:
                doc_id = conn.execute(
                    insert(documents).values(
                        user_id=user_id,
                        title=body.title,
                        mime=body.mime,
                        storage_path=body.storage_path,
                        status='pending'
                    ).returning(documents.c.id)
                ).scalar_one()
        except Exception as e:
            print(f"DB error creating document: {e}")
            raise HTTPException(500, 'Erro ao criar registro do documento')

        # Agendar processamento em background
        background_tasks.add_task(process_ingestion_task, str(doc_id), user_id, body.storage_path)

        return {'document_id': str(doc_id), 'status': 'pending', 'message': 'Processamento iniciado'}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error in /ingest: {e}")
        raise HTTPException(500, 'Erro interno no servidor')

from .multimodal import process_image, process_audio, process_video

import asyncio

async def process_ingestion_task(doc_id: str, user_id: str, storage_path: str):
    """
    Task assíncrona que processa o documento (PDF, Imagem, Áudio, Vídeo).
    Executa tarefas bloqueantes num thread pool para não travar o FastAPI.
    """
    print(f"Starting ingestion for {doc_id}")
    loop = asyncio.get_running_loop()
    
    try:
        # Update status -> processing
        with engine.begin() as conn:
            # Pegar mime type atual
            mime = conn.execute(sqltext("SELECT mime FROM documents WHERE id = :id"), {'id': doc_id}).scalar()
            conn.execute(sqltext("UPDATE documents SET status = 'processing' WHERE id = :id"), {'id': doc_id})

        # Download com streaming e validação de tamanho
        url = create_signed_url(storage_path, 600)
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream('GET', url) as r:
                r.raise_for_status()
                chunks_data = []
                total_size = 0
                async for chunk in r.aiter_bytes(chunk_size=8192):
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        raise Exception(f'Arquivo muito grande (>{MAX_FILE_SIZE // (1024*1024)}MB)')
                    chunks_data.append(chunk)
                data = b''.join(chunks_data)

        # Processar baseado no MIME type
        recs = []  # Lista de chunks com metadados

        if mime == 'application/pdf':
            # BLOCKING: executando em thread
            pages = await loop.run_in_executor(None, extract_pages_from_pdf, data)
            if not pages:
                raise Exception('Nenhum conteúdo extraído do PDF')

            # Processar cada página separadamente para preservar número da página
            chunk_idx = 0
            for page_num, page_text in enumerate(pages, start=1):
                if not page_text.strip():
                    continue  # Pular páginas vazias
                page_chunks = chunk_text(page_text)
                for ck in page_chunks:
                    recs.append({
                        'user_id': user_id,
                        'document_id': doc_id,
                        'page': page_num,  # Página real do PDF
                        'chunk_index': chunk_idx,
                        'text': ck
                    })
                    chunk_idx += 1

        elif mime.startswith('image/'):
            # BLOCKING: Network I/O
            text_content = await loop.run_in_executor(None, process_image, data)
            if not text_content:
                raise Exception('Nenhum conteúdo extraído da imagem')
            for ci, ck in enumerate(chunk_text(text_content)):
                recs.append({'user_id': user_id, 'document_id': doc_id, 'page': 1, 'chunk_index': ci, 'text': ck})

        elif mime.startswith('audio/'):
            # BLOCKING: Network I/O
            filename = storage_path.split('/')[-1]
            text_content = await loop.run_in_executor(None, lambda: process_audio(data, filename=filename))
            if not text_content:
                raise Exception('Nenhum conteúdo extraído do áudio')
            for ci, ck in enumerate(chunk_text(text_content)):
                recs.append({'user_id': user_id, 'document_id': doc_id, 'page': 1, 'chunk_index': ci, 'text': ck})

        elif mime.startswith('video/'):
            # BLOCKING: Network I/O
            text_content = await loop.run_in_executor(None, process_video, data)
            if not text_content:
                raise Exception('Nenhum conteúdo extraído do vídeo')
            for ci, ck in enumerate(chunk_text(text_content)):
                recs.append({'user_id': user_id, 'document_id': doc_id, 'page': 1, 'chunk_index': ci, 'text': ck})

        else:
            raise Exception(f'Formato não suportado: {mime}')

        if not recs:
            raise Exception('Nenhum chunk gerado')
        
        # Embed & Save
        if recs:
             # Batch processing
            batch_size = 64
            for i in range(0, len(recs), batch_size):
                batch = [r['text'] for r in recs[i:i+batch_size]]
                # BLOCKING: Network I/O
                vecs = await loop.run_in_executor(None, embed_texts, batch)
                for r, v in zip(recs[i:i+batch_size], vecs):
                    r['embedding'] = v

            # Salvar chunks
            with engine.begin() as conn:
                conn.execute(insert(chunks), recs)

        # Update status -> completed
        with engine.begin() as conn:
            conn.execute(sqltext("UPDATE documents SET status = 'completed' WHERE id = :id"), {'id': doc_id})
            
        print(f"Ingestion for {doc_id} completed successfully.")

    except Exception as e:
        print(f"Ingestion failed for {doc_id}: {e}")
        with engine.begin() as conn:
            conn.execute(
                sqltext("UPDATE documents SET status = 'failed', meta = jsonb_build_object('error', :err) WHERE id = :id"), 
                {'id': doc_id, 'err': str(e)}
            )

@app.get('/documents')
async def list_documents(request: Request, query: str | None = None, semantic_query: str | None = None):
    user_id = await require_user(request)
    
    # Se tiver busca semântica, filtramos IDs via vector search primeiro
    relevant_ids = None
    if semantic_query:
        from .rag import embed_texts
        embeddings = embed_texts([semantic_query])
        if not embeddings:
            raise HTTPException(503, 'Erro ao gerar embedding para busca')
        qvec = embeddings[0]
        # Converter embedding para string no formato PostgreSQL vector
        qvec_str = '[' + ','.join(map(str, qvec)) + ']'
        # Buscar documentos que tenham chunks similares (Top 50 docs)
        with engine.begin() as conn:
            # Usando GROUP BY + MAX para evitar erro "ORDER BY must appear in select list"
            sql = sqltext("""
                SELECT document_id, MAX(1 - (embedding <=> CAST(:qvec AS vector))) as max_score
                FROM chunks
                WHERE 1 - (embedding <=> CAST(:qvec AS vector)) > 0.15
                GROUP BY document_id
                ORDER BY max_score DESC
                LIMIT 50
            """)
            rows = conn.execute(sql, {'qvec': qvec_str}).fetchall()
            relevant_ids = [str(r[0]) for r in rows]

            if not relevant_ids:
                return {'items': []}

    with engine.begin() as conn:
        # Se tiver relevant_ids, filtramos por eles. Se não, trazemos todos.
        # Ordenação: Se busca semântica, manter ordem de relevância (fazer no python ou CASE no SQL).
        # Simplificação: Trazemos os docs e ordenamos no python se precisar, ou apenas filtramos.
        
        base_sql = "select id, title, mime, status from documents"
        params = {}
        
        # Agora é TEAM HUB: removemos filtro user_id para ver tudo
        # Mas mantemos ordem cronológica se não houver busca
        
        if relevant_ids is not None:
             # Postgres não tem order by field facilemente sem extensions, vamos pegar e ordenar no python
             # Usando ANY para filtrar
             base_sql += " WHERE id = ANY(:ids)"
             params['ids'] = relevant_ids
        else:
             base_sql += " ORDER BY uploaded_at DESC"

        rows = conn.execute(sqltext(base_sql), params).mappings().all()

    items = [{'id': str(r['id']), 'title': r['title'], 'mime': r['mime'], 'status': r['status']} for r in rows]
    
    # Se busca por texto exato (filtro de nome)
    if query:
        ql = query.lower()
        items = [i for i in items if ql in i['title'].lower()]
        
    # Se busca semântica, ordenar items pela ordem de relevant_ids
    if relevant_ids:
        # Criar map de ordem
        order_map = {rid: i for i, rid in enumerate(relevant_ids)}
        items.sort(key=lambda x: order_map.get(x['id'], 999))

    return {'items': items}

class ChatBody(BaseModel):
    message: str
    document_ids: list[str] | None = None
    thread_id: str | None = None  # Para continuar conversas existentes

    class Config:
        str_strip_whitespace = True


# ==========================================
# THREAD & MESSAGE MANAGEMENT
# ==========================================

def create_thread(user_id: str) -> str:
    """Cria uma nova thread de conversação para o usuário."""
    with engine.begin() as conn:
        thread_id = conn.execute(
            insert(threads).values(user_id=user_id).returning(threads.c.id)
        ).scalar_one()
    return str(thread_id)


def validate_thread_ownership(thread_id: str, user_id: str) -> bool:
    """
    Valida se a thread pertence ao usuário.
    Retorna True se pertence, False caso contrário.
    """
    with engine.begin() as conn:
        result = conn.execute(
            sqltext("SELECT user_id FROM threads WHERE id = :thread_id"),
            {'thread_id': thread_id}
        ).first()

    if not result:
        return False

    return str(result[0]) == user_id


def get_thread_history(thread_id: str, user_id: str, limit: int = 20) -> list[dict]:
    """
    Recupera o histórico de mensagens de uma thread.
    Valida que a thread pertence ao usuário antes de retornar.
    """
    with engine.begin() as conn:
        # Query com validação de ownership - só retorna se thread pertencer ao user
        # Adaptado para schema real: role (text), meta (jsonb), created_at
        rows = conn.execute(
            sqltext("""
                SELECT m.role, m.content, m.meta, m.created_at
                FROM messages m
                JOIN threads t ON m.thread_id = t.id
                WHERE m.thread_id = :thread_id
                  AND t.user_id = :user_id
                ORDER BY m.created_at ASC
                LIMIT :limit
            """),
            {'thread_id': thread_id, 'user_id': user_id, 'limit': limit}
        ).mappings().all()

    return [
        {
            'role': r['role'],  # 'user' ou 'assistant'
            'content': r['content'],
            'citations': r['meta'].get('citations') if r['meta'] else None
        }
        for r in rows
    ]


def save_message(user_id: str, thread_id: str, from_user: bool, content: str, citations: list | None = None):
    """
    Salva uma mensagem na thread.
    A validação de ownership deve ser feita ANTES de chamar esta função.
    Adaptado para schema real: role (text), meta (jsonb)
    """
    import json
    role = 'user' if from_user else 'assistant'
    meta = {'citations': citations} if citations else None

    with engine.begin() as conn:
        conn.execute(
            sqltext("""
                INSERT INTO messages (id, thread_id, role, content, meta, created_at)
                VALUES (gen_random_uuid(), :thread_id, :role, :content, :meta, NOW())
            """),
            {
                'thread_id': thread_id,
                'role': role,
                'content': content,
                'meta': json.dumps(meta) if meta else None
            }
        )


@app.post('/chat')
async def chat(request: Request, body: ChatBody):
    """
    Endpoint de chat com o agente RAG.
    Suporta conversas persistentes via thread_id.
    Cada usuário tem suas próprias threads isoladas.
    """
    try:
        user_id = await require_user(request)

        # Validação de input
        if not body.message or len(body.message.strip()) == 0:
            raise HTTPException(400, 'Mensagem não pode estar vazia')

        if len(body.message) > 10000:
            raise HTTPException(400, 'Mensagem muito longa (máximo 10000 caracteres)')

        # Gerenciar thread com validação de ownership
        thread_id = body.thread_id
        if thread_id:
            # Validar que a thread pertence ao usuário atual
            if not validate_thread_ownership(thread_id, user_id):
                raise HTTPException(403, 'Thread não pertence a este usuário')
            print(f"[CHAT] Continuando thread existente: {thread_id}")
        else:
            # Criar nova thread se não fornecida
            thread_id = create_thread(user_id)
            print(f"[CHAT] Nova thread criada: {thread_id}")

        # Carregar histórico da conversa (já valida ownership internamente)
        history = get_thread_history(thread_id, user_id)
        print(f"[CHAT] Thread {thread_id} - Histórico: {len(history)} mensagens")

        # Salvar mensagem do usuário
        save_message(user_id, thread_id, from_user=True, content=body.message)

        trace = start_trace('chat', {'user_id': user_id, 'thread_id': thread_id, 'history_length': len(history)})

        # Executar agente com histórico
        try:
            import asyncio
            loop = asyncio.get_running_loop()

            # Passar histórico para o agente
            out = await loop.run_in_executor(
                None,
                run_agent,
                user_id,
                body.message,
                body.document_ids,
                history  # NOVO: passar histórico
            )

            # Salvar resposta do agente
            save_message(
                user_id,
                thread_id,
                from_user=False,
                content=out.get('answer', ''),
                citations=out.get('citations')
            )

            end_trace(trace, 'success', {'citations_count': len(out.get('citations', []))})

            # Retornar com thread_id para o frontend continuar a conversa
            return {
                **out,
                'thread_id': thread_id
            }

        except Exception as e:
            end_trace(trace, 'error', {'error': str(e)})
            print(f"Agent execution error: {e}")
            raise HTTPException(500, 'Erro ao processar sua pergunta')

    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error in /chat: {e}")
        raise HTTPException(500, 'Erro interno no chat')

@app.get('/document/{doc_id}/preview')
async def preview(request: Request, doc_id: str):
    # Autenticação necessária, mas documentos são compartilhados (Team Hub)
    await require_user(request)
    with engine.begin() as conn:
        row = conn.execute(sqltext('select storage_path from documents where id=:id'), {'id': doc_id}).first()
    if not row: raise HTTPException(404, 'Documento não encontrado')
    url = create_signed_url(row[0], 600)
    return {'signed_url': url}
