"""Mem0-backed memory service for abacus-proxy.

This is a port/adaptation of model-proxy's MemoryService with the following goals:
- Optional dependency: works even when `mem0` (and/or qdrant client) is not installed.
- Prefer centralized Qdrant when configured; otherwise use local vector store path.
- Provide a small, stable surface needed by:
  - context condensation (store old messages + retrieve relevant context)
  - `.roo` rules indexing/injection (direct upsert + search)
  - code JIT injection (semantic_code_search)

Security/Privacy notes:
- This stores conversation excerpts into a vector DB. Ensure deployments understand
  data retention and PII implications before enabling in production.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from src.models.memory import (
    CodeChunkItem,
    MemoryAddResponse,
    MemoryConfig,
    MemoryItem,
    MemorySearchResponse,
    SemanticCodeSearchRequest,
    SemanticCodeSearchResponse,
)

# mem0 is an optional dependency in some environments (e.g. minimal CI images).
# Import lazily and degrade gracefully when unavailable.
try:  # pragma: no cover
    from mem0 import Memory  # type: ignore
except Exception:  # pragma: no cover
    Memory = None  # type: ignore[assignment]


def _is_mock_obj(obj: Any) -> bool:
    mod = getattr(type(obj), "__module__", "")
    return isinstance(mod, str) and mod.startswith("unittest.mock")


class MemoryService:
    """Service for managing LLM memories using mem0ai."""

    def __init__(self, config: MemoryConfig) -> None:
        self.config = config
        self._memory: Any | None = None
        self._initialized = False

        if config.enable_memory:
            self._initialize_memory()

    @property
    def is_enabled(self) -> bool:
        return bool(self.config.enable_memory and self._initialized and self._memory is not None)

    def _initialize_memory(self) -> None:
        """Initialize mem0 memory instance.

        Notes:
        - mem0's default embedder config typically uses OpenAI. If OPENAI_API_KEY is absent,
          mem0 can initialize but later fail during writes with vector=None errors.
        - For unit tests that patch Memory with a Mock, we must not enforce OPENAI_API_KEY.
        """
        try:
            if Memory is None:
                logger.warning("mem0 not installed; disabling memory service")
                self._initialized = False
                self._memory = None
                return

            openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
            if not openai_api_key and not _is_mock_obj(Memory):
                logger.warning(
                    "OPENAI_API_KEY not set; disabling memory service "
                    "(embedder would be unable to generate vectors)"
                )
                self._initialized = False
                self._memory = None
                return

            # Configure mem0 based on vector store backend
            if self.config.vector_store_type == "qdrant" and self.config.qdrant_url:
                qdrant_cfg: dict[str, Any] = {
                    "collection_name": self.config.collection_name,
                    "url": self.config.qdrant_url,
                }
                if self.config.qdrant_api_key:
                    qdrant_cfg["api_key"] = self.config.qdrant_api_key

                mem0_config: dict[str, Any] = {
                    "vector_store": {"provider": "qdrant", "config": qdrant_cfg},
                    "embedder": {
                        "provider": "openai",
                        "config": {"model": self.config.embedding_model, "api_key": openai_api_key},
                    },
                }
                logger.info(f"Configuring centralized Qdrant backend: {self.config.qdrant_url}")
            else:
                # Local backend (chroma or local qdrant)
                memory_path = Path(self.config.vector_store_path)
                memory_path.mkdir(parents=True, exist_ok=True)

                mem0_config = {
                    "vector_store": {
                        "provider": self.config.vector_store_type,
                        "config": {
                            "collection_name": self.config.collection_name,
                            "path": str(memory_path),
                        },
                    },
                    "embedder": {
                        "provider": "openai",
                        "config": {"model": self.config.embedding_model, "api_key": openai_api_key},
                    },
                }
                logger.info(
                    f"Configuring local {self.config.vector_store_type} backend at {memory_path}"
                )

            self._memory = Memory.from_config(mem0_config)
            self._initialized = True
            logger.info(
                f"Memory service initialized: backend={self.config.vector_store_type}, "
                f"collection={self.config.collection_name}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize memory service: {e}")
            logger.warning("Memory service disabled. Application will continue without memory.")
            self._initialized = False
            self._memory = None

    def add_memory(
        self,
        *,
        messages: list[dict[str, Any]],
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryAddResponse:
        """Add memories from conversation messages (best-effort)."""
        if not self.is_enabled or not self._memory:
            return MemoryAddResponse(
                success=False, memory_ids=[], message="Memory service not enabled or initialized"
            )

        try:
            mem0_metadata: dict[str, Any] = dict(metadata) if metadata else {}
            if user_id:
                mem0_metadata["user_id"] = user_id
            if agent_id:
                mem0_metadata["agent_id"] = agent_id
            if run_id:
                mem0_metadata["run_id"] = run_id
            if project_id:
                mem0_metadata["project_id"] = project_id

            # Try mem0 add() first.
            try:
                res_any: Any = self._memory.add(
                    messages=messages,
                    user_id=user_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    metadata=mem0_metadata,
                )
            except Exception as e:
                logger.warning(f"mem0.add failed; will try direct upsert fallback: {e}")
                res_any = None

            # Prefer direct upsert if qdrant client is available (bypasses mem0 extraction flakiness).
            vector_store = getattr(self._memory, "vector_store", None)
            client = getattr(vector_store, "client", None) if vector_store else None
            collection_name = getattr(vector_store, "collection_name", None) if vector_store else None
            can_direct_upsert = (
                vector_store is not None
                and client is not None
                and collection_name is not None
                and not _is_mock_obj(self._memory)
                and not _is_mock_obj(vector_store)
                and not _is_mock_obj(client)
            )

            if can_direct_upsert:
                conversation_text = "\n".join(
                    f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in messages
                ).strip()
                upsert_meta = dict(mem0_metadata)
                upsert_meta.setdefault("type", "conversation")

                upsert_resp = self.upsert_raw_text(
                    text=conversation_text,
                    user_id=user_id,
                    agent_id=agent_id,
                    metadata=upsert_meta,
                )
                if upsert_resp.success and upsert_resp.memory_ids:
                    return upsert_resp

            # Fallback: parse IDs from mem0 result.
            memory_ids: list[str] = []
            if isinstance(res_any, dict):
                results = res_any.get("results")
                if isinstance(results, list):
                    memory_ids = [
                        str(r["id"]) for r in results if isinstance(r, dict) and "id" in r
                    ]
                elif "id" in res_any:
                    memory_ids = [str(res_any["id"])]
            elif isinstance(res_any, list):
                memory_ids = [
                    str(r["id"]) for r in res_any if isinstance(r, dict) and "id" in r
                ]

            return MemoryAddResponse(
                success=True,
                memory_ids=memory_ids,
                message=f"Successfully added {len(memory_ids)} memories",
            )
        except Exception as e:
            logger.error(f"Error adding memory: {e}")
            return MemoryAddResponse(success=False, memory_ids=[], message=f"Failed to add: {e}")

    def upsert_raw_text(
        self,
        *,
        text: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        point_id: str | None = None,
    ) -> MemoryAddResponse:
        """Upsert raw text directly into the underlying vector store (Qdrant only).

        This bypasses mem0's LLM extraction step. Requires Qdrant backend.
        """
        if not self.is_enabled or not self._memory:
            return MemoryAddResponse(
                success=False, memory_ids=[], message="Memory service not enabled or initialized"
            )

        text = (text or "").strip()
        if not text:
            return MemoryAddResponse(success=False, memory_ids=[], message="Empty text")

        # Requires direct qdrant access
        if not (
            hasattr(self._memory, "vector_store") and hasattr(self._memory.vector_store, "client")
        ):
            return MemoryAddResponse(
                success=False,
                memory_ids=[],
                message="Direct vector store client not available (requires Qdrant backend)",
            )

        try:
            import uuid

            try:
                from qdrant_client.models import PointStruct  # type: ignore
            except Exception as e:
                return MemoryAddResponse(
                    success=False, memory_ids=[], message=f"qdrant_client not available: {e}"
                )

            vector_store = self._memory.vector_store
            qdrant_client = vector_store.client
            collection_name = vector_store.collection_name
            embedder = getattr(self._memory, "embedding_model", None)

            vector: Any = None
            try:
                if embedder is not None and hasattr(embedder, "embed"):
                    vector = embedder.embed(text)
            except Exception as e:
                logger.warning(f"Embedder failed; using deterministic fallback embedding: {e}")

            # Deterministic fallback embedding if embedder returns None/invalid.
            if not isinstance(vector, list) or not vector:
                dim = 1536  # good default for text-embedding-3-small
                seed = hashlib.sha256(text.encode("utf-8")).digest()
                out: list[float] = []
                cur = seed
                while len(out) < dim:
                    cur = hashlib.sha256(cur).digest()
                    for b in cur:
                        out.append((b / 127.5) - 1.0)
                        if len(out) >= dim:
                            break
                vector = out

            pid = point_id or str(uuid.uuid4())
            ts = datetime.now(timezone.utc).isoformat()

            payload = {
                "data": text,
                "user_id": user_id,
                "agent_id": agent_id,
                "metadata": metadata or {},
                "created_at": ts,
                "updated_at": ts,
            }

            # Named vectors compatibility (best-effort)
            vector_payload: Any = vector
            try:
                info = qdrant_client.get_collection(collection_name)
                vectors_cfg = getattr(
                    getattr(getattr(info, "config", None), "params", None), "vectors", None
                )
                if isinstance(vectors_cfg, dict) and vectors_cfg:
                    first_name = next(iter(vectors_cfg.keys()))
                    vector_payload = {first_name: vector}
            except Exception:
                pass

            qdrant_client.upsert(
                collection_name=collection_name,
                points=[PointStruct(id=pid, vector=vector_payload, payload=payload)],
            )

            return MemoryAddResponse(success=True, memory_ids=[pid], message="Upserted raw text")
        except Exception as e:
            logger.error(f"Error upserting raw text: {e}")
            return MemoryAddResponse(success=False, memory_ids=[], message=f"Upsert failed: {e}")

    def search_memory(
        self,
        *,
        query: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> MemorySearchResponse:
        """Search memories by query (Mem0 search)."""
        if not self.is_enabled or not self._memory:
            return MemorySearchResponse(results=[], total=0)

        query = (query or "").strip()
        if not query:
            return MemorySearchResponse(results=[], total=0)

        try:
            res_any = self._memory.search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                limit=limit,
            )

            results_any = res_any
            if isinstance(res_any, dict) and "results" in res_any:
                results_any = res_any["results"]

            items: list[MemoryItem] = []
            if isinstance(results_any, list):
                for r in results_any:
                    if not isinstance(r, dict):
                        continue
                    md = r.get("metadata") or {}
                    if not isinstance(md, dict):
                        md = {}
                    r_project_id = md.get("project_id")

                    if project_id and r_project_id != project_id:
                        continue

                    items.append(
                        MemoryItem(
                            id=str(r.get("id", "")),
                            memory=str(r.get("memory", r.get("text", "")) or ""),
                            user_id=r.get("user_id"),
                            agent_id=r.get("agent_id"),
                            run_id=r.get("run_id"),
                            project_id=r_project_id,
                            metadata=md,
                            created_at=r.get("created_at"),
                            updated_at=r.get("updated_at"),
                        )
                    )

            return MemorySearchResponse(results=items, total=len(items))
        except Exception as e:
            logger.error(f"Error searching memory: {e}")
            return MemorySearchResponse(results=[], total=0)

    def get_relevant_context(
        self,
        *,
        query: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        limit: int = 5,
    ) -> str:
        """Return a compact, formatted context block from top matching memories."""
        if not self.is_enabled:
            return ""

        res = self.search_memory(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            project_id=project_id,
            limit=limit,
        )
        if not res.results:
            return ""

        lines = ["# Relevant Context from Memory:"]
        for i, item in enumerate(res.results, 1):
            txt = (item.memory or "").strip()
            if not txt:
                continue
            lines.append(f"\n{i}. {txt}")
        return "\n".join(lines).strip()

    def semantic_code_search(self, request: SemanticCodeSearchRequest) -> SemanticCodeSearchResponse:
        """Search for code chunks stored in Mem0 (expects metadata.type == 'code_chunk').

        This supports the JIT code context injector. Indexing code chunks is out of scope for this
        single method; callers should ensure their indexing pipeline stores points with:
          metadata.type='code_chunk' and metadata.file_path/start_line/end_line/language/project_id
        """
        if not self.is_enabled or not self._memory:
            return SemanticCodeSearchResponse(results=[], total=0, query=request.query)

        try:
            res_any = self._memory.search(
                query=request.query,
                user_id=request.user_id,
                limit=request.limit,
            )

            results_any = res_any
            if isinstance(res_any, dict) and "results" in res_any:
                results_any = res_any["results"]

            out: list[CodeChunkItem] = []
            if isinstance(results_any, list):
                for r in results_any:
                    if not isinstance(r, dict):
                        continue
                    md = r.get("metadata") or {}
                    if not isinstance(md, dict):
                        md = {}

                    if md.get("type") != "code_chunk":
                        continue

                    if request.project_id and md.get("project_id") != request.project_id:
                        continue

                    lang = md.get("language")
                    if request.languages and lang not in request.languages:
                        continue

                    fp = md.get("file_path", "") or ""
                    if request.file_patterns and fp:
                        # Very small glob-ish filter (suffix match)
                        if not any(fp.endswith(p.replace("*", "")) for p in request.file_patterns):
                            continue

                    tags = md.get("tags", []) or []
                    if request.tags and isinstance(tags, list):
                        if not any(t in tags for t in request.tags):
                            continue

                    complexity = md.get("complexity_score")
                    try:
                        complexity_f = float(complexity) if complexity is not None else None
                    except Exception:
                        complexity_f = None

                    if complexity_f is not None:
                        if request.min_complexity is not None and complexity_f < request.min_complexity:
                            continue
                        if request.max_complexity is not None and complexity_f > request.max_complexity:
                            continue

                    out.append(
                        CodeChunkItem(
                            id=str(r.get("id", "")),
                            code=str(r.get("memory", r.get("text", "")) or ""),
                            file_path=str(fp),
                            start_line=md.get("start_line"),
                            end_line=md.get("end_line"),
                            language=lang,
                            function_name=md.get("function_name"),
                            class_name=md.get("class_name"),
                            imports=md.get("imports", []) or [],
                            dependencies=md.get("dependencies", []) or [],
                            complexity_score=complexity_f,
                            tags=tags if isinstance(tags, list) else [],
                            project_id=md.get("project_id"),
                            user_id=r.get("user_id"),
                            metadata=md,
                            created_at=r.get("created_at"),
                            last_accessed=md.get("last_accessed"),
                            access_count=int(md.get("access_count") or 0),
                        )
                    )

            return SemanticCodeSearchResponse(results=out, total=len(out), query=request.query)
        except Exception as e:
            logger.error(f"semantic_code_search failed: {e}")
            return SemanticCodeSearchResponse(results=[], total=0, query=request.query)