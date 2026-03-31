"""
Agno Agent Integration - Document Q&A Agent with RAG

Este módulo implementa um agente usando o framework Agno para responder
perguntas sobre documentos do usuário usando busca semântica (RAG).
"""

import os
import uuid as uuid_mod
from typing import Dict, List
from sqlalchemy import text as sqltext, create_engine
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from .rag import embed_texts

# Configurações
SIM_THRESHOLD = float(os.getenv('SIM_THRESHOLD', '0.2'))
DB_URL = os.getenv('SUPABASE_DB_URL')
MODEL = os.getenv('MODEL', 'gpt-4o-mini')

# Database engine para custom tools
engine = create_engine(DB_URL, pool_pre_ping=True)


# ==========================================
# CUSTOM TOOLS - Document Search
# ==========================================

def search_documents(user_id: str, query: str, limit: int = 8, document_ids: List[str] | None = None) -> List[Dict]:
    """
    Busca semanticamente nos documentos do usuário usa embeddings vetoriais.
    Permite filtrar por IDs específicos.
    """
    try:
        # Gerar embedding da query
        query_embedding = embed_texts([query])[0]

        # Converter embedding para string no formato PostgreSQL vector
        qvec_str = '[' + ','.join(map(str, query_embedding)) + ']'

        # Query base - usando CAST em vez de :: para evitar conflito com SQLAlchemy
        base_sql = """
                SELECT
                    c.id,
                    c.document_id,
                    d.title as document_title,
                    c.page,
                    left(c.text, 500) as snippet,
                    1 - (c.embedding <=> CAST(:qvec AS vector)) as score
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE 1 - (c.embedding <=> CAST(:qvec AS vector)) >= :threshold
        """

        # Filtros
        # AGORA É TEAM HUB: removemos filtro user_id para busca global,
        # mas filtro de document_ids é aplicado se fornecido.
        params = {
            'qvec': qvec_str,
            'threshold': SIM_THRESHOLD,
            'limit': limit
        }

        if document_ids:
            # Validar UUIDs para evitar SQL injection
            valid_ids = []
            for did in document_ids:
                try:
                    uuid_mod.UUID(did)  # Valida formato UUID
                    valid_ids.append(did)
                except (ValueError, AttributeError):
                    continue  # Ignora IDs inválidos

            if valid_ids:
                # Converter lista para formato de array PostgreSQL: {uuid1,uuid2,...}
                params['document_ids'] = '{' + ','.join(valid_ids) + '}'
                base_sql += " AND c.document_id = ANY(CAST(:document_ids AS uuid[]))"

        base_sql += " ORDER BY c.embedding <=> CAST(:qvec AS vector) LIMIT :limit"

        with engine.begin() as conn:
            rows = conn.execute(sqltext(base_sql), params).mappings().all()

        results = [{
            'document_id': str(r['document_id']),
            'document_title': r['document_title'],
            'page': r['page'],
            'snippet': r['snippet'],
            'score': float(r['score'])
        } for r in rows]

        return results

    except Exception as e:
        print(f"Erro ao buscar documentos: {e}")
        return []


def list_user_documents(user_id: str) -> List[Dict]:
    """
    Lista todos os documentos DO HUB (Team Space).
    """
    try:
        with engine.begin() as conn:
            sql = sqltext("""
                SELECT
                    id,
                    title,
                    mime,
                    uploaded_at
                FROM documents
                ORDER BY uploaded_at DESC
                LIMIT 50
            """)

            rows = conn.execute(sql).mappings().all()

        return [{
            'document_id': str(r['id']),
            'title': r['title'],
            'mime': r['mime'],
            'uploaded_at': str(r['uploaded_at'])
        } for r in rows]

    except Exception as e:
        print(f"Erro ao listar documentos: {e}")
        return []


# ==========================================
# AGNO AGENT - Document Assistant (Gemini)
# ==========================================

def create_document_agent(user_id: str, document_ids: List[str] | None = None) -> tuple[Agent, dict]:
    """
    Cria um agente Agno configurado para responder perguntas (GEMINI BRAIN).
    """
    # Contexto compartilhado
    context = {'last_search_results': []}

    # ... (tools search_tool e list_tool mantidas iguais) ...
    def search_tool(query: str, limit: int = 8) -> str:
        results = search_documents(user_id, query, limit, document_ids)
        context['last_search_results'] = results
        if not results: return "Nenhum documento relevante encontrado."
        formatted = []
        for i, r in enumerate(results[:5], 1):
            formatted.append(f"[{i}] {r['document_title']} (p.{r['page']}): {r['snippet']}")
        return "\n".join(formatted)

    def list_tool() -> str:
        docs = list_user_documents(user_id)
        if not docs: return "Nenhum documento disponível."
        return "\n".join([f"{d['title']}" for d in docs[:10]])

    # Criar agent com OpenAI GPT-4o-mini
    agent = Agent(
        name="Team Hub Assistant",
        model=OpenAIChat(id=os.getenv('MODEL', 'gpt-4o-mini')),
        tools=[search_tool, list_tool],
        instructions=[
            "Você é um assistente do Team Hub que responde perguntas sobre documentos compartilhados.",
            "Responda perguntas baseando-se EXCLUSIVAMENTE nos documentos encontrados via busca.",
            "SEMPRE use a ferramenta search_tool para buscar informações antes de responder.",
            "SEMPRE cite a fonte (nome do documento e página) nas suas respostas.",
            "Se não encontrar informações relevantes, diga claramente que não há dados sobre o tema nos documentos.",
        ],
        markdown=True,
        add_datetime_to_context=True,
    )

    return agent, context


# ==========================================
# INTERFACE PÚBLICA
# ==========================================

def build_agent_messages(history: list[dict]) -> list[dict]:
    """
    Convert thread history to Agno message format.
    Each message gets a clear role boundary that prevents injection.
    """
    if not history:
        return []

    agent_messages = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        agent_messages.append({"role": role, "content": msg["content"]})
    return agent_messages


def run_agent(
    user_id: str,
    message: str,
    document_ids: list[str] | None = None,
    history: list[dict] | None = None
) -> dict:
    """
    Executa o agente para responder uma pergunta, opcionalmente filtrada por documentos.
    Suporta histórico de conversação via mensagens estruturadas.
    """
    try:
        agent, context = create_document_agent(user_id, document_ids)

        # Build structured messages from history + current message
        messages = build_agent_messages(history) if history else []
        messages.append({"role": "user", "content": message})

        if history:
            print(f"[AGENT] Running with {len(history)} history messages")
        else:
            print("[AGENT] Running without history (new conversation)")

        # Execute agent - try structured messages first, fall back to single message
        try:
            response = agent.run(messages=messages)
        except TypeError:
            # Agno version may not support messages= parameter
            # Fall back to single message (loses history but prevents injection)
            print("[AGENT] Falling back to single message (messages= not supported)")
            response = agent.run(message)

        # Extract citations
        search_results = context.get('last_search_results', [])
        citations = []
        if search_results:
            for result in search_results[:3]:
                citations.append({
                    'document_id': result['document_id'],
                    'document_title': result['document_title'],
                    'page': result['page'],
                    'snippet': result['snippet'][:200] + '...' if len(result['snippet']) > 200 else result['snippet']
                })

        return {
            'answer': response.content,
            'citations': citations
        }

    except Exception as e:
        print(f"Agent error: {e}")
        raise
