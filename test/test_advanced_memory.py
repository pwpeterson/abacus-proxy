"""Tests for advanced memory features."""

from __future__ import annotations

import pytest

from src.models.memory import MemoryConfig
from src.utils.code_indexer import CodeIndexer
from src.utils.decision_logger import DecisionLogger
from src.utils.error_tracker import ErrorTracker
from src.utils.memory_analytics import MemoryAnalytics
from src.utils.memory_service import MemoryService


@pytest.fixture
def memory_service():
    """Create a test memory service."""
    config = MemoryConfig(
        enable_memory=False,  # Disabled for unit tests
        vector_store_type="qdrant",
        vector_store_path="./test_memory_data",
        embedding_model="text-embedding-3-small",
        collection_name="test_collection",
    )
    return MemoryService(config)


@pytest.fixture
def decision_logger(memory_service):
    """Create a decision logger."""
    return DecisionLogger(memory_service)


@pytest.fixture
def error_tracker(memory_service):
    """Create an error tracker."""
    return ErrorTracker(memory_service)


@pytest.fixture
def code_indexer(memory_service):
    """Create a code indexer."""
    return CodeIndexer(memory_service)


@pytest.fixture
def memory_analytics(memory_service):
    """Create memory analytics."""
    return MemoryAnalytics(memory_service)


def test_decision_logger_log_decision(decision_logger):
    """Test logging a decision."""
    result = decision_logger.log_decision(
        title="Test Decision",
        description="This is a test decision",
        rationale="For testing purposes",
        alternatives=["Alternative 1", "Alternative 2"],
        consequences=["Consequence 1"],
        tags=["test", "architecture"],
    )

    assert isinstance(result, dict)
    assert "success" in result
    # Will be False since memory service is disabled
    assert result["success"] is False


def test_decision_logger_search_decisions(decision_logger):
    """Test searching decisions."""
    decisions = decision_logger.search_decisions(
        query="test decision",
        tags=["architecture"],
        limit=10,
    )

    assert isinstance(decisions, list)
    # Will be empty since memory service is disabled
    assert len(decisions) == 0


def test_error_tracker_log_error(error_tracker):
    """Test logging an error."""
    try:
        raise ValueError("Test error")
    except Exception as e:
        result = error_tracker.log_error(
            error=e,
            context="Test context",
            severity="error",
            component="test_component",
            tags=["test"],
        )

    assert isinstance(result, dict)
    assert "success" in result
    # Will be False since memory service is disabled
    assert result["success"] is False


def test_error_tracker_search_similar_errors(error_tracker):
    """Test searching for similar errors."""
    similar = error_tracker.search_similar_errors(
        error="ValueError: test",
        limit=5,
    )

    assert isinstance(similar, list)
    # Will be empty since memory service is disabled
    assert len(similar) == 0


def test_error_tracker_get_error_patterns(error_tracker):
    """Test getting error patterns."""
    patterns = error_tracker.get_error_patterns(
        component="test_component",
        limit=20,
    )

    assert isinstance(patterns, dict)
    assert "patterns" in patterns
    assert "total_errors" in patterns
    # Will be empty since memory service is disabled
    assert patterns["total_errors"] == 0


def test_error_tracker_generate_signature():
    """Test error signature generation."""
    sig1 = ErrorTracker._generate_error_signature("ValueError", "File not found: /path/to/file.txt")
    sig2 = ErrorTracker._generate_error_signature("ValueError", "File not found: /other/path/file.txt")

    # Signatures should be similar for same error type with different paths
    assert isinstance(sig1, str)
    assert isinstance(sig2, str)
    assert len(sig1) == 8
    assert len(sig2) == 8


def test_code_indexer_detect_language(code_indexer):
    """Test language detection."""
    from pathlib import Path

    assert code_indexer._detect_language(Path("test.py")) == "python"
    assert code_indexer._detect_language(Path("test.js")) == "javascript"
    assert code_indexer._detect_language(Path("test.ts")) == "typescript"
    assert code_indexer._detect_language(Path("test.go")) == "go"
    assert code_indexer._detect_language(Path("test.rs")) == "rust"
    assert code_indexer._detect_language(Path("test.unknown")) == "unknown"


def test_code_indexer_parse_python_structure(code_indexer):
    """Test parsing Python code structure."""
    code = """
import os
from pathlib import Path

class MyClass:
    def method1(self):
        pass

def my_function():
    pass
"""

    structure = code_indexer._parse_code_structure(code, "python")

    assert "functions" in structure
    assert "classes" in structure
    assert "imports" in structure
    assert "my_function" in structure["functions"]
    assert "MyClass" in structure["classes"]
    assert "os" in structure["imports"]
    assert "pathlib" in structure["imports"]


def test_code_indexer_chunk_code(code_indexer):
    """Test code chunking."""
    code = "\n".join([f"line {i}" for i in range(100)])

    chunks = code_indexer._chunk_code(code, chunk_size=20, overlap=5)

    assert len(chunks) > 0
    assert all("code" in chunk for chunk in chunks)
    assert all("start_line" in chunk for chunk in chunks)
    assert all("end_line" in chunk for chunk in chunks)

    # Check overlap
    if len(chunks) > 1:
        first_chunk_end = chunks[0]["end_line"]
        second_chunk_start = chunks[1]["start_line"]
        # Should have overlap
        assert second_chunk_start < first_chunk_end


def test_code_indexer_generate_tags(code_indexer):
    """Test tag generation."""
    chunk = {
        "functions": ["func1", "func2"],
        "classes": ["Class1"],
        "imports": ["os", "sys"],
        "complexity": 8,
    }

    structure = {
        "functions": ["func1", "func2"],
        "classes": ["Class1"],
    }

    tags = code_indexer._generate_tags(chunk, structure)

    assert "has_functions" in tags
    assert "has_classes" in tags
    assert "has_imports" in tags
    assert "medium_complexity" in tags


def test_memory_analytics_get_usage_stats(memory_analytics):
    """Test getting usage stats."""
    stats = memory_analytics.get_usage_stats()

    assert isinstance(stats, dict)
    assert "enabled" in stats
    # Will be False since memory service is disabled
    assert stats["enabled"] is False


def test_memory_analytics_get_memory_health(memory_analytics):
    """Test getting memory health."""
    health = memory_analytics.get_memory_health()

    assert isinstance(health, dict)
    assert "status" in health
    # Will be "disabled" since memory service is disabled
    assert health["status"] == "disabled"


def test_memory_analytics_get_type_distribution(memory_analytics):
    """Test getting type distribution."""
    distribution = memory_analytics.get_type_distribution()

    assert isinstance(distribution, dict)
    assert "types" in distribution
    assert "total" in distribution
    # Will be empty since memory service is disabled
    assert distribution["total"] == 0


def test_memory_analytics_get_recent_activity(memory_analytics):
    """Test getting recent activity."""
    activity = memory_analytics.get_recent_activity(hours=24)

    assert isinstance(activity, dict)
    assert "recent_memories" in activity
    assert "count" in activity
    # Will be empty since memory service is disabled
    assert activity["count"] == 0


def test_memory_analytics_get_optimization_recommendations(memory_analytics):
    """Test getting optimization recommendations."""
    recommendations = memory_analytics.get_optimization_recommendations()

    assert isinstance(recommendations, list)
    # Will be empty since memory service is disabled
    assert len(recommendations) == 0


def test_decision_logger_format_list():
    """Test list formatting."""
    items = ["Item 1", "Item 2", "Item 3"]
    formatted = DecisionLogger._format_list(items)

    assert "- Item 1" in formatted
    assert "- Item 2" in formatted
    assert "- Item 3" in formatted

    # Test empty list
    assert DecisionLogger._format_list([]) == "None"