from sqlalchemy import Table, Column, String, Integer, Boolean, Text, JSON, TIMESTAMP, ForeignKey, MetaData
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
import uuid

md = MetaData()

documents = Table(
    'documents', md,
    Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column('user_id', UUID(as_uuid=True), nullable=False),
    Column('title', Text, nullable=False),
    Column('mime', Text),
    Column('storage_path', Text, nullable=False),
    Column('meta', JSON),
    Column('status', String, default='pending'),  # pending, processing, completed, failed
    Column('uploaded_at', TIMESTAMP(timezone=True), server_default=func.now())
)

chunks = Table(
    'chunks', md,
    Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column('user_id', UUID(as_uuid=True), nullable=False),
    Column('document_id', UUID(as_uuid=True), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
    Column('page', Integer),
    Column('chunk_index', Integer),
    Column('text', Text, nullable=False),
    Column('embedding', Vector(1536))
)

threads = Table(
    'threads', md,
    Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column('user_id', UUID(as_uuid=True), nullable=False),
    Column('title', Text),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('updated_at', TIMESTAMP(timezone=True), server_default=func.now())
)

messages = Table(
    'messages', md,
    Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column('thread_id', UUID(as_uuid=True), ForeignKey('threads.id', ondelete='CASCADE'), nullable=False),
    Column('role', Text, nullable=False),  # 'user' | 'assistant' | 'system'
    Column('content', Text, nullable=False),
    Column('meta', JSON),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now())
)
