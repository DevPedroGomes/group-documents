from sqlalchemy import create_engine
from .models import metadata

from app.config.settings import get_settings


def get_engine():
    settings = get_settings()
    return create_engine(settings.supabase_db_url, pool_pre_ping=True)


engine = get_engine()
