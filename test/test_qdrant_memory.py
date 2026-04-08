"""Test Qdrant memory backend integration.

Adapted from model-proxy's test_qdrant_memory.py with additions for
advanced memory features.

This test requires:
1. Qdrant running on localhost:6333 (or QDRANT_URL set)
2. OPENAI_API_KEY environment variable set

Run with: pytest test/test_qdrant_memory.py -v
"""

import os
import pytest
from pathlib import Path

from src.models.memory import MemoryConfig, SemanticCodeSearchRequest
from src.utils.code_indexer import CodeIndexer
from src.utils.decision_logger import DecisionLogger
from src.utils.error_tracker import ErrorTracker
from src.utils.memory_analytics import MemoryAnalytics
from src.utils.memory_service import MemoryService


@pytest.fixture
def qdrant_config():
    """Create Qdrant memory configuration."""
    return MemoryConfig(
        enable_memory=True,
        vector_store_type="qdrant",
        qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY"),
        collection_name="abacus_proxy_test",
        embedding_model="text-embedding-3-small",
    )


@pytest.fixture
def memory_service(qdrant_config):
    """Create memory service with Qdrant backend."""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    service = MemoryService(config=qdrant_config)
    yield service
    
    # Cleanup: Note - abacus-proxy doesn't have delete_all_memories yet
    # Individual test cleanup should be done within tests if needed


@pytest.fixture
def decision_logger(memory_service):
    """Create decision logger."""
    return DecisionLogger(memory_service)


@pytest.fixture
def error_tracker(memory_service):
    """Create error tracker."""
    return ErrorTracker(memory_service)


@pytest.fixture
def code_indexer(memory_service):
    """Create code indexer."""
    return CodeIndexer(memory_service)


@pytest.fixture
def analytics(memory_service):
    """Create memory analytics."""
    return MemoryAnalytics(memory_service)


class TestQdrantConnection:
    """Test Qdrant connection and basic operations."""

    def test_connection(self, memory_service):
        """Test Qdrant connection."""
        assert memory_service.config.enable_memory is True
        assert memory_service.config.vector_store_type == "qdrant"

    def test_add_memory(self, memory_service):
        """Test adding memory to Qdrant."""
        messages = [
            {"role": "user", "content": "What is Qdrant?"},
            {"role": "assistant", "content": "Qdrant is a vector database."},
        ]

        result = memory_service.add_memory(
            messages=messages,
            user_id="test_user",
            project_id="abacus-proxy",
            metadata={"test": True},
        )

        assert result.success is True
        assert len(result.memory_ids) > 0

    def test_search_memory(self, memory_service):
        """Test searching memory in Qdrant."""
        # Add test memory
        messages = [
            {"role": "user", "content": "How does vector search work?"},
            {"role": "assistant", "content": "Vector search uses embeddings for similarity."},
        ]

        memory_service.add_memory(
            messages=messages,
            user_id="test_user",
            project_id="abacus-proxy",
        )

        # Search
        result = memory_service.search_memory(
            query="vector search embeddings",
            user_id="test_user",
            project_id="abacus-proxy",
            limit=5,
        )

        assert len(result.results) > 0
        assert "vector" in result.results[0].memory.lower() or "embedding" in result.results[0].memory.lower()

    def test_get_all_memories(self, memory_service):
        """Test getting all memories from Qdrant."""
        # Add test memories
        for i in range(3):
            memory_service.add_memory(
                messages=[
                    {"role": "user", "content": f"Test message {i}"},
                    {"role": "assistant", "content": f"Response {i}"},
                ],
                user_id="test_user",
                project_id="abacus-proxy",
            )

        # Get all
        result = memory_service.get_all_memories(
            user_id="test_user",
            project_id="abacus-proxy",
            limit=10,
        )

        assert len(result.results) >= 3

    def test_delete_memory(self, memory_service):
        """Test deleting memory from Qdrant."""
        # Add memory
        result = memory_service.add_memory(
            messages=[
                {"role": "user", "content": "Delete this"},
                {"role": "assistant", "content": "Will be deleted"},
            ],
            user_id="test_user",
            project_id="abacus-proxy",
        )

        memory_id = result.memory_ids[0]

        # Delete
        delete_result = memory_service.delete_memory(memory_id=memory_id)
        assert delete_result.success is True


class TestDecisionLogging:
    """Test decision logging with Qdrant."""

    def test_log_decision(self, decision_logger):
        """Test logging a decision."""
        result = decision_logger.log_decision(
            title="Use FastAPI for API",
            description="Selected FastAPI as the web framework",
            rationale="Modern, fast, type-safe",
            alternatives=["Flask", "Django"],
            consequences=["Need async support", "Better performance"],
            tags=["architecture", "framework"],
            user_id="test_user",
            project_id="abacus-proxy",
        )

        assert result.get("success") is True
        assert "decision_id" in result

    def test_search_decisions(self, decision_logger):
        """Test searching decisions."""
        # Log decision
        decision_logger.log_decision(
            title="Database Selection",
            description="Choose Qdrant for vector storage",
            rationale="Best performance for semantic search",
            tags=["database", "architecture"],
            user_id="test_user",
            project_id="abacus-proxy",
        )

        # Search
        decisions = decision_logger.search_decisions(
            query="database vector",
            tags=["database"],
            user_id="test_user",
            project_id="abacus-proxy",
            limit=5,
        )

        assert len(decisions) > 0
        assert any("database" in d.lower() or "qdrant" in d.lower() for d in decisions)


class TestErrorTracking:
    """Test error tracking with Qdrant."""

    def test_log_error(self, error_tracker):
        """Test logging an error."""
        test_error = ValueError("Test error message")
        result = error_tracker.log_error(
            error=test_error,
            context="During test execution",
            severity="error",
            component="test_suite",
            solution="Fix the test",
            tags=["test", "error"],
            user_id="test_user",
            project_id="abacus-proxy",
        )

        assert result.get("success") is True
        assert "error_id" in result
        assert "error_signature" in result

    def test_search_similar_errors(self, error_tracker):
        """Test searching for similar errors."""
        # Log error
        error_tracker.log_error(
            error=ValueError("Configuration missing"),
            context="Startup",
            severity="critical",
            component="config",
            user_id="test_user",
            project_id="abacus-proxy",
        )

        # Search similar
        similar = error_tracker.search_similar_errors(
            error="ValueError: configuration",
            user_id="test_user",
            project_id="abacus-proxy",
            limit=5,
        )

        assert len(similar) > 0

    def test_error_patterns(self, error_tracker):
        """Test getting error patterns."""
        # Log multiple errors
        for i in range(3):
            error_tracker.log_error(
                error=RuntimeError(f"Runtime error {i}"),
                context=f"Context {i}",
                severity="error",
                component="runtime",
                user_id="test_user",
                project_id="abacus-proxy",
            )

        # Get patterns
        patterns = error_tracker.get_error_patterns(
            user_id="test_user",
            project_id="abacus-proxy",
            limit=10,
        )

        assert patterns["total_errors"] >= 3
        assert patterns["unique_patterns"] >= 1


class TestCodeIndexing:
    """Test code indexing with Qdrant."""

    def test_index_file(self, code_indexer):
        """Test indexing a Python file."""
        test_file = Path(__file__)
        result = code_indexer.index_file(
            file_path=test_file,
            user_id="test_user",
            project_id="abacus-proxy",
            chunk_size=30,
            overlap=5,
        )

        assert result.get("success") is True
        assert result["chunks_indexed"] > 0

    def test_search_code(self, code_indexer, memory_service):
        """Test searching indexed code."""
        # Index this file
        code_indexer.index_file(
            file_path=Path(__file__),
            user_id="test_user",
            project_id="abacus-proxy",
        )

        # Search
        search_result = memory_service.semantic_code_search(
            SemanticCodeSearchRequest(
                query="test qdrant connection",
                user_id="test_user",
                project_id="abacus-proxy",
                limit=3,
            )
        )

        assert len(search_result.results) > 0


class TestMemoryAnalytics:
    """Test memory analytics with Qdrant."""

    def test_usage_stats(self, analytics, memory_service):
        """Test getting usage statistics."""
        # Add some test data
        memory_service.add_memory(
            messages=[
                {"role": "user", "content": "Test"},
                {"role": "assistant", "content": "Response"},
            ],
            user_id="test_user",
            project_id="abacus-proxy",
        )

        stats = analytics.get_usage_stats(
            user_id="test_user",
            project_id="abacus-proxy",
        )

        assert stats.get("enabled") is True
        assert stats.get("total_memories", 0) > 0

    def test_memory_health(self, analytics):
        """Test memory health check."""
        health = analytics.get_memory_health()

        assert health.get("status") in ["healthy", "degraded", "unhealthy"]
        assert health.get("backend") == "qdrant"

    def test_type_distribution(self, analytics, memory_service):
        """Test getting type distribution."""
        # Add memories of different types
        memory_service.add_memory(
            messages=[{"role": "user", "content": "Test"}],
            user_id="test_user",
            project_id="abacus-proxy",
        )

        distribution = analytics.get_type_distribution(
            user_id="test_user",
            project_id="abacus-proxy",
        )

        assert "total" in distribution
        assert "types" in distribution

    def test_optimization_recommendations(self, analytics):
        """Test getting optimization recommendations."""
        recommendations = analytics.get_optimization_recommendations(
            user_id="test_user",
            project_id="abacus-proxy",
        )

        assert isinstance(recommendations, list)
        # Should have at least some recommendations
        for rec in recommendations:
            assert "severity" in rec
            assert "message" in rec


class TestMultiUser:
    """Test multi-user scenarios with Qdrant."""

    def test_user_isolation(self, memory_service):
        """Test that users' memories are isolated."""
        # Add memory for user 1
        memory_service.add_memory(
            messages=[
                {"role": "user", "content": "User 1 message"},
                {"role": "assistant", "content": "User 1 response"},
            ],
            user_id="test_user",
            project_id="abacus-proxy",
        )

        # Add memory for user 2
        memory_service.add_memory(
            messages=[
                {"role": "user", "content": "User 2 message"},
                {"role": "assistant", "content": "User 2 response"},
            ],
            user_id="test_user_2",
            project_id="abacus-proxy",
        )

        # Search user 1's memories
        result1 = memory_service.search_memory(
            query="message",
            user_id="test_user",
            project_id="abacus-proxy",
            limit=10,
        )

        # Search user 2's memories
        result2 = memory_service.search_memory(
            query="message",
            user_id="test_user_2",
            project_id="abacus-proxy",
            limit=10,
        )

        # Verify isolation
        user1_content = " ".join([r.memory for r in result1.results])
        user2_content = " ".join([r.memory for r in result2.results])

        assert "User 1" in user1_content
        assert "User 2" not in user1_content
        assert "User 2" in user2_content
        assert "User 1" not in user2_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])