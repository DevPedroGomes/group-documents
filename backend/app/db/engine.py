from sqlalchemy import create_engine
from .models import metadata

from app.config.settings import get_settings


def get_engine():
    settings = get_settings()
    # Convert asyncpg URL to psycopg2 for synchronous SQLAlchemy usage
    db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    return create_engine(db_url, pool_pre_ping=True)


engine = get_engine()
