"""BrainHub Team API — FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.app_name)

    # CORS
    cors_origins = [o.strip() for o in settings.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Include routers
    from app.api.routes.documents import router as documents_router
    from app.api.routes.chat import router as chat_router

    app.include_router(documents_router)
    app.include_router(chat_router)

    return app


app = create_app()
