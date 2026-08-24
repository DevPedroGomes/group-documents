"""BrainHub Team API — FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config.settings import get_settings
from app.api.rate_limit import limiter
from app.db.migrate import run_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Aplica migrations pendentes antes de aceitar trafego.

    Deliberadamente FATAL: se o schema nao puder ser levado ao estado esperado,
    a app nao sobe. E o oposto do que acontecia antes — schema divergente do
    codigo, app subindo normal, e toda ingestao falhando em silencio.
    """
    run_migrations()

    from agent_ops.queue import criar_pool

    # Um pool por processo, aberto no boot: abrir por requisicao criaria uma
    # conexao nova a cada upload. Se `criar_pool` levantar, o erro propaga
    # daqui e a app nao termina de subir — o oposto de subir sem `state.fila`
    # e quebrar so no primeiro upload.
    app.state.fila = await criar_pool()

    yield

    await app.state.fila.aclose()


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        lifespan=_lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    # Wire slowapi
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/demo-limits", include_in_schema=False)
    async def demo_limits():
        """Quanto sobrou do teto de hoje.

        Publico de proposito: um visitante que bate num limite deve conseguir
        ver que aquilo e uma decisao de projeto, nao um app quebrado. Nao expoe
        nada sensivel — so contadores e os proprios limites.

        Os limites vao como argumento porque o pacote nao conhece os tipos de
        cota deste app; quem os nomeia e quem os configura.
        """
        from agent_ops import metering

        s = get_settings()
        return await metering.panorama(
            {"chat": s.daily_chat_limit, "ingest": s.daily_ingest_limit}
        )

    # CORS
    cors_origins = [o.strip() for o in settings.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Include routers
    from app.api.routes.auth import router as auth_router
    from app.api.routes.documents import router as documents_router
    from app.api.routes.chat import router as chat_router

    app.include_router(auth_router)
    app.include_router(documents_router)
    app.include_router(chat_router)

    return app


app = create_app()
