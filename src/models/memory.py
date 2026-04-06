"""Memory-related models for abacus-proxy.

These are intentionally minimal but compatible with the subset of Mem0-backed features
we want to port from model-proxy:
- MemoryService add/search/relevant-context
- Semantic code search (for code JIT injection)
- Rule shard indexing metadata
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """Configuration for Mem0-backed memory features."""

    enable_memory: bool = Field(default=True)

    # Vector store backend used by mem0
    vector_store_type: str = Field(default="qdrant")  # e.g. "qdrant", "chroma"
    vector_store_path: str = Field(default="./memory_data")

    # Embedding model used by mem0 (typically OpenAI embedding model name)
    embedding_model: str = Field(default="text-embedding-3-small")

    # Collection name in vector store
    collection_name: str = Field(default="abacus_proxy_memory")

    # Centralized Qdrant (optional)
    qdrant_url: str | None = Field(default=None)
    qdrant_api_key: str | None = Field(default=None)


class MemoryAddResponse(BaseModel):
    success: bool = Field(default=False)
    memory_ids: list[str] = Field(default_factory=list)
    message: str = Field(default="")


class MemoryItem(BaseModel):
    id: str = Field(default="")
    memory: str = Field(default="")

    user_id: str | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)
    project_id: str | None = Field(default=None)

    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = Field(default=None)
    updated_at: str | None = Field(default=None)


class MemorySearchResponse(BaseModel):
    results: list[MemoryItem] = Field(default_factory=list)
    total: int = Field(default=0)


class SemanticCodeSearchRequest(BaseModel):
    query: str

    # Optional filters
    user_id: str | None = None
    project_id: str | None = None

    limit: int = Field(default=10, ge=1, le=100)

    languages: list[str] | None = None
    file_patterns: list[str] | None = None
    tags: list[str] | None = None

    min_complexity: float | None = None
    max_complexity: float | None = None


class CodeChunkItem(BaseModel):
    id: str = Field(default="")
    code: str = Field(default="")

    file_path: str = Field(default="")
    start_line: int | None = None
    end_line: int | None = None

    language: str | None = None
    function_name: str | None = None
    class_name: str | None = None

    imports: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    complexity_score: float | None = None

    tags: list[str] = Field(default_factory=list)

    project_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_at: str | None = None
    last_accessed: str | None = None
    access_count: int = Field(default=0)


class SemanticCodeSearchResponse(BaseModel):
    results: list[CodeChunkItem] = Field(default_factory=list)
    total: int = Field(default=0)

    query: str = Field(default="")
    search_time_seconds: float = Field(default=0.0)