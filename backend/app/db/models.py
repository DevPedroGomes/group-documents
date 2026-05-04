from sqlalchemy import (
    Table, Column, String, Integer, Text, JSON, TIMESTAMP,
    ForeignKey, MetaData, Float, Boolean,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
import uuid

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("email", String(255), unique=True, nullable=False, index=True),
    Column("password_hash", String(255), nullable=False),
    Column("full_name", String(255), nullable=True),
    Column("is_active", Boolean, default=True),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

documents = Table(
    "documents", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("title", Text, nullable=False),
    Column("mime", Text),
    Column("storage_path", Text, nullable=False),
    Column("meta", JSON),
    Column("status", String, default="pending"),
    Column("summary", Text),
    Column("chunk_count", Integer, default=0),
    Column("uploaded_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

chunks = Table(
    "chunks", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("page", Integer),
    Column("chunk_index", Integer),
    Column("content", Text, nullable=False),
    Column("enriched_content", Text),
    Column("embedding", Vector(1536)),
    Column("token_count", Integer),
    # Column name in DB is `metadata` but SQLAlchemy reserves the `metadata` attribute on Table,
    # so we use key="meta_json" to expose as `chunks.c.meta_json` while writing column `metadata`.
    Column("metadata", JSON, key="meta_json"),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

threads = Table(
    "threads", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("title", Text),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

messages = Table(
    "messages", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("thread_id", UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("citations", JSON),
    Column("sources", ARRAY(UUID(as_uuid=True))),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

semantic_cache = Table(
    "semantic_cache", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("query_hash", Text, nullable=False),
    Column("query_embedding", Vector(1536)),
    Column("query_text", Text, nullable=False),
    Column("response_text", Text, nullable=False),
    Column("citations", JSON),
    Column("document_ids", ARRAY(UUID(as_uuid=True))),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("expires_at", TIMESTAMP(timezone=True)),
)
