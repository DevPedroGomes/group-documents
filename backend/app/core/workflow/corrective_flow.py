"""
LangGraph Corrective RAG workflow.

Pipeline: Retrieve → Rerank → Grade → [Transform → Web Search] → Generate
"""

import logging
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END

from app.core.rag.retriever import retrieve_documents
from app.core.rag.grader import grade_documents
from app.core.rag.transformer import transform_query
from app.core.rag.generator import generate_answer

logger = logging.getLogger(__name__)


class WorkflowStep(TypedDict):
    step: str
    status: str
    details: str


class GraphState(TypedDict):
    question: str
    original_question: str
    document_ids: Optional[list[str]]
    user_id: str
    history: Optional[list[dict]]
    documents: list[dict]
    answer: str
    citations: list[dict]
    workflow: list[WorkflowStep]
    needs_web_search: bool
    used_web_search: bool
    was_corrected: bool


# --- Nodes ---

def retrieve_node(state: GraphState) -> dict:
    """Retrieve documents using multi-query hybrid search + reranking."""
    documents = retrieve_documents(
        question=state["question"],
        document_ids=state.get("document_ids"),
        top_k=5,
    )

    workflow = state["workflow"] + [{
        "step": "retrieve",
        "status": "completed",
        "details": f"Found {len(documents)} relevant chunks",
    }]

    return {"documents": documents, "workflow": workflow}


def grade_node(state: GraphState) -> dict:
    """Grade documents by relevance score threshold."""
    filtered, needs_web_search = grade_documents(state["documents"])

    workflow = state["workflow"] + [{
        "step": "grade",
        "status": "completed",
        "details": f"Kept {len(filtered)}/{len(state['documents'])} documents"
            + (" — triggering web search" if needs_web_search else ""),
    }]

    return {
        "documents": filtered,
        "needs_web_search": needs_web_search,
        "workflow": workflow,
    }


def transform_node(state: GraphState) -> dict:
    """Transform query for better retrieval."""
    transformed = transform_query(state["question"])

    workflow = state["workflow"] + [{
        "step": "transform",
        "status": "completed",
        "details": f"Rewrote query: {transformed[:100]}",
    }]

    return {
        "question": transformed,
        "was_corrected": True,
        "workflow": workflow,
    }


def web_search_node(state: GraphState) -> dict:
    """Search the web as fallback when documents are insufficient."""
    workflow_step: WorkflowStep = {
        "step": "web_search",
        "status": "completed",
        "details": "Web search not configured — answering from available documents",
    }

    try:
        from app.config.settings import get_settings
        settings = get_settings()

        if settings.tavily_api_key:
            from tavily import TavilyClient
            client = TavilyClient(api_key=settings.tavily_api_key)
            results = client.search(state["question"], max_results=3)

            web_docs = []
            for r in results.get("results", []):
                web_docs.append({
                    "id": "web",
                    "document_id": "web",
                    "document_title": r.get("title", "Web Result"),
                    "page": 0,
                    "snippet": r.get("content", "")[:500],
                    "relevance_score": r.get("score", 0.5),
                })

            workflow_step["details"] = f"Found {len(web_docs)} web results"
            return {
                "documents": state["documents"] + web_docs,
                "used_web_search": True,
                "workflow": state["workflow"] + [workflow_step],
            }
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        workflow_step["details"] = f"Web search failed: {e}"

    return {
        "used_web_search": False,
        "workflow": state["workflow"] + [workflow_step],
    }


def generate_node(state: GraphState) -> dict:
    """Generate answer from documents using Claude Sonnet."""
    answer = generate_answer(
        question=state["original_question"],
        documents=state["documents"],
        history=state.get("history"),
    )

    # Extract citations from top documents
    citations = []
    for doc in state["documents"][:5]:
        if doc.get("document_id") != "web":
            citations.append({
                "document_id": doc["document_id"],
                "document_title": doc["document_title"],
                "page": doc["page"],
                "snippet": doc["snippet"][:200],
            })

    workflow = state["workflow"] + [{
        "step": "generate",
        "status": "completed",
        "details": f"Generated answer ({len(answer)} chars)",
    }]

    return {"answer": answer, "citations": citations, "workflow": workflow}


# --- Routing ---

def should_transform(state: GraphState) -> str:
    if state["needs_web_search"]:
        return "transform"
    return "generate"


# --- Graph ---

def build_graph() -> StateGraph:
    workflow = StateGraph(GraphState)

    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("grade", grade_node)
    workflow.add_node("transform", transform_node)
    workflow.add_node("web_search", web_search_node)
    workflow.add_node("generate", generate_node)

    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "grade")
    workflow.add_conditional_edges("grade", should_transform, {
        "transform": "transform",
        "generate": "generate",
    })
    workflow.add_edge("transform", "web_search")
    workflow.add_edge("web_search", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()


# Compiled graph (singleton)
corrective_rag_graph = build_graph()


def run_corrective_rag(
    question: str,
    user_id: str,
    document_ids: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
) -> dict:
    """Run the full Corrective RAG pipeline. Returns answer + citations + workflow."""
    initial_state: GraphState = {
        "question": question,
        "original_question": question,
        "document_ids": document_ids,
        "user_id": user_id,
        "history": history,
        "documents": [],
        "answer": "",
        "citations": [],
        "workflow": [],
        "needs_web_search": False,
        "used_web_search": False,
        "was_corrected": False,
    }

    final_state = corrective_rag_graph.invoke(initial_state)

    return {
        "answer": final_state["answer"],
        "citations": final_state["citations"],
        "workflow": final_state["workflow"],
        "was_corrected": final_state["was_corrected"],
        "used_web_search": final_state["used_web_search"],
    }
