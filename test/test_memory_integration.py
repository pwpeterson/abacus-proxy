"""Live integration test for memory service with actual mem0.

Adapted from model-proxy's test_memory_live.py with additions for
advanced memory features (decision logging, error tracking, code indexing, analytics).

This test requires:
1. OPENAI_API_KEY environment variable set
2. Qdrant running (optional, will use Chroma if not available)

Run with: python test/test_memory_integration.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models.memory import MemoryConfig
from src.utils.code_indexer import CodeIndexer
from src.utils.context_manager import ContextManager
from src.utils.decision_logger import DecisionLogger
from src.utils.error_tracker import ErrorTracker
from src.utils.memory_analytics import MemoryAnalytics
from src.utils.memory_service import MemoryService


def print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def print_result(test_name: str, success: bool, details: str = "") -> None:
    """Print test result."""
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"{status} - {test_name}")
    if details:
        print(f"    {details}")


async def test_memory_service() -> bool:
    """Test basic memory service operations."""
    print_section("Testing Memory Service")

    # Check for API key
    if not os.getenv("OPENAI_API_KEY"):
        print_result("API Key Check", False, "OPENAI_API_KEY not set")
        return False
    print_result("API Key Check", True, "OPENAI_API_KEY found")

    # Initialize memory service
    try:
        config = MemoryConfig(
            enable_memory=True,
            vector_store_path="./test_memory_data",
            vector_store_type="chroma",
            embedding_model="text-embedding-3-small",
            collection_name="abacus_proxy_test",
        )
        memory_service = MemoryService(config=config)
        print_result("Memory Service Init", True, f"Using {config.vector_store_type}")
    except Exception as e:
        print_result("Memory Service Init", False, str(e))
        return False

    # Test 1: Add memory
    try:
        test_messages = [
            {"role": "user", "content": "What is abacus-proxy?"},
            {
                "role": "assistant",
                "content": "abacus-proxy is an intelligent proxy for RouteLLM with memory features.",
            },
        ]

        result = memory_service.add_memory(
            messages=test_messages,
            user_id="test_user",
            agent_id="test_agent",
            project_id="abacus-proxy",
            metadata={"test": True, "topic": "abacus-proxy"},
        )

        if result.success and len(result.memory_ids) > 0:
            print_result("Add Memory", True, f"Added {len(result.memory_ids)} memories")
        else:
            print_result("Add Memory", False, result.message)
            return False
    except Exception as e:
        print_result("Add Memory", False, str(e))
        return False

    # Test 2: Search memory
    try:
        search_result = memory_service.search_memory(
            query="abacus-proxy features",
            user_id="test_user",
            project_id="abacus-proxy",
            limit=5,
        )

        if len(search_result.results) > 0:
            print_result(
                "Search Memory",
                True,
                f"Found {len(search_result.results)} relevant memories",
            )
        else:
            print_result("Search Memory", False, "No memories found")
    except Exception as e:
        print_result("Search Memory", False, str(e))
        return False

    # Test 3: Get relevant context
    try:
        context = memory_service.get_relevant_context(
            query="abacus-proxy",
            user_id="test_user",
            project_id="abacus-proxy",
            limit=3,
        )

        if context:
            print_result("Get Context", True, f"Retrieved {len(context)} chars of context")
        else:
            print_result("Get Context", False, "No context retrieved")
    except Exception as e:
        print_result("Get Context", False, str(e))
        return False

    return True


async def test_decision_logging(memory_service: MemoryService) -> bool:
    """Test decision logging features."""
    print_section("Testing Decision Logging")

    decision_logger = DecisionLogger(memory_service)

    # Test 1: Log a decision
    try:
        result = decision_logger.log_decision(
            title="Use Qdrant for Vector Storage",
            description="Selected Qdrant as the vector database for semantic search",
            rationale="Qdrant provides better performance and scalability",
            alternatives=[
                "Chroma - simpler but less scalable",
                "Pinecone - cloud-only, vendor lock-in",
            ],
            consequences=[
                "Need to run Qdrant service",
                "Better query performance",
            ],
            tags=["architecture", "database"],
            project_id="abacus-proxy",
        )

        if result.get("success"):
            print_result("Log Decision", True, f"Decision ID: {result.get('decision_id')}")
        else:
            print_result("Log Decision", False, result.get("message", "Unknown error"))
    except Exception as e:
        print_result("Log Decision", False, str(e))
        return False

    # Test 2: Search decisions
    try:
        decisions = decision_logger.search_decisions(
            query="vector database",
            tags=["architecture"],
            project_id="abacus-proxy",
            limit=5,
        )

        if len(decisions) > 0:
            print_result("Search Decisions", True, f"Found {len(decisions)} decisions")
        else:
            print_result("Search Decisions", False, "No decisions found")
    except Exception as e:
        print_result("Search Decisions", False, str(e))
        return False

    return True


async def test_error_tracking(memory_service: MemoryService) -> bool:
    """Test error tracking features."""
    print_section("Testing Error Tracking")

    error_tracker = ErrorTracker(memory_service)

    # Test 1: Log an error
    try:
        test_error = ValueError("Test configuration error")
        result = error_tracker.log_error(
            error=test_error,
            context="During startup configuration",
            severity="error",
            component="config_loader",
            solution="Check environment variables",
            tags=["config", "startup"],
            project_id="abacus-proxy",
        )

        if result.get("success"):
            print_result(
                "Log Error",
                True,
                f"Error ID: {result.get('error_id')}, Signature: {result.get('error_signature')}",
            )
        else:
            print_result("Log Error", False, result.get("message", "Unknown error"))
    except Exception as e:
        print_result("Log Error", False, str(e))
        return False

    # Test 2: Search similar errors
    try:
        similar = error_tracker.search_similar_errors(
            error="ValueError: configuration",
            project_id="abacus-proxy",
            limit=5,
        )

        if len(similar) > 0:
            print_result("Search Similar Errors", True, f"Found {len(similar)} similar errors")
        else:
            print_result("Search Similar Errors", False, "No similar errors found")
    except Exception as e:
        print_result("Search Similar Errors", False, str(e))
        return False

    # Test 3: Get error patterns
    try:
        patterns = error_tracker.get_error_patterns(
            project_id="abacus-proxy",
            limit=10,
        )

        print_result(
            "Get Error Patterns",
            True,
            f"Total: {patterns['total_errors']}, Unique: {patterns['unique_patterns']}",
        )
    except Exception as e:
        print_result("Get Error Patterns", False, str(e))
        return False

    return True


async def test_code_indexing(memory_service: MemoryService) -> bool:
    """Test code indexing features."""
    print_section("Testing Code Indexing")

    code_indexer = CodeIndexer(memory_service)

    # Test 1: Index a file
    try:
        test_file = Path(__file__)  # Index this test file
        result = code_indexer.index_file(
            file_path=test_file,
            project_id="abacus-proxy",
            chunk_size=30,
            overlap=5,
        )

        if result.get("success"):
            print_result(
                "Index File",
                True,
                f"Indexed {result['chunks_indexed']} chunks from {result['file_path']}",
            )
        else:
            print_result("Index File", False, result.get("message", "Unknown error"))
    except Exception as e:
        print_result("Index File", False, str(e))
        return False

    # Test 2: Search indexed code
    try:
        from src.models.memory import SemanticCodeSearchRequest

        search_result = memory_service.semantic_code_search(
            SemanticCodeSearchRequest(
                query="test memory service",
                project_id="abacus-proxy",
                limit=3,
            )
        )

        if len(search_result.results) > 0:
            print_result(
                "Search Code",
                True,
                f"Found {len(search_result.results)} code chunks",
            )
        else:
            print_result("Search Code", False, "No code chunks found")
    except Exception as e:
        print_result("Search Code", False, str(e))
        return False

    return True


async def test_memory_analytics(memory_service: MemoryService) -> bool:
    """Test memory analytics features."""
    print_section("Testing Memory Analytics")

    analytics = MemoryAnalytics(memory_service)

    # Test 1: Get usage stats
    try:
        stats = analytics.get_usage_stats(project_id="abacus-proxy")

        if stats.get("enabled"):
            print_result(
                "Usage Stats",
                True,
                f"Total: {stats.get('total_memories', 0)}, Types: {len(stats.get('type_breakdown', {}))}",
            )
        else:
            print_result("Usage Stats", False, stats.get("message", "Not enabled"))
    except Exception as e:
        print_result("Usage Stats", False, str(e))
        return False

    # Test 2: Get memory health
    try:
        health = analytics.get_memory_health()

        if health.get("status") == "healthy":
            print_result("Memory Health", True, f"Backend: {health.get('backend')}")
        else:
            print_result("Memory Health", False, f"Status: {health.get('status')}")
    except Exception as e:
        print_result("Memory Health", False, str(e))
        return False

    # Test 3: Get type distribution
    try:
        distribution = analytics.get_type_distribution(project_id="abacus-proxy")

        print_result(
            "Type Distribution",
            True,
            f"Total: {distribution.get('total', 0)}, Types: {len(distribution.get('types', {}))}",
        )
    except Exception as e:
        print_result("Type Distribution", False, str(e))
        return False

    # Test 4: Get optimization recommendations
    try:
        recommendations = analytics.get_optimization_recommendations(project_id="abacus-proxy")

        print_result(
            "Optimization Recommendations",
            True,
            f"Found {len(recommendations)} recommendations",
        )
        for rec in recommendations[:3]:
            print(f"    [{rec['severity']}] {rec['message']}")
    except Exception as e:
        print_result("Optimization Recommendations", False, str(e))
        return False

    return True


async def main() -> None:
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  Abacus-Proxy Memory Integration Test Suite")
    print("=" * 60)

    # Initialize memory service for advanced tests
    try:
        config = MemoryConfig(
            enable_memory=True,
            vector_store_path="./test_memory_data",
            vector_store_type="chroma",
            embedding_model="text-embedding-3-small",
            collection_name="abacus_proxy_test",
        )
        memory_service = MemoryService(config=config)
    except Exception as e:
        print(f"❌ Failed to initialize memory service: {e}")
        sys.exit(1)

    # Run tests
    memory_ok = await test_memory_service()
    decision_ok = await test_decision_logging(memory_service)
    error_ok = await test_error_tracking(memory_service)
    code_ok = await test_code_indexing(memory_service)
    analytics_ok = await test_memory_analytics(memory_service)

    # Summary
    print_section("Test Summary")
    print(f"Memory Service: {'✅ PASS' if memory_ok else '❌ FAIL'}")
    print(f"Decision Logging: {'✅ PASS' if decision_ok else '❌ FAIL'}")
    print(f"Error Tracking: {'✅ PASS' if error_ok else '❌ FAIL'}")
    print(f"Code Indexing: {'✅ PASS' if code_ok else '❌ FAIL'}")
    print(f"Memory Analytics: {'✅ PASS' if analytics_ok else '❌ FAIL'}")

    if all([memory_ok, decision_ok, error_ok, code_ok, analytics_ok]):
        print("\n🎉 All memory integration tests passed!")
        print("\nNext steps:")
        print("1. Start abacus-proxy: python -m src.main_fastapi")
        print("2. Test with real conversations")
        print("3. Monitor logs for context condensation")
        print("4. Use advanced memory features via MCP tools")
    else:
        print("\n❌ Some tests failed. Please check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())