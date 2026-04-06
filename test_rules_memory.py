#!/usr/bin/env python3
"""Test script for Rules Memory System integration.

This script verifies that:
1. Rules indexer can read and chunk .roo/rules files
2. Rules can be indexed into Mem0
3. Rules can be retrieved based on queries
4. Rules prompt injector works correctly
"""

import asyncio
import os
from pathlib import Path

from loguru import logger

from src.models.memory import MemoryConfig
from src.utils.memory_service import MemoryService
from src.utils.rules_indexer import index_rules_into_mem0, iter_rule_shards
from src.utils.rules_prompt_injector import inject_retrieved_rules


def test_rule_shards():
    """Test that rules can be read and chunked into shards."""
    logger.info("Testing rule shard generation...")
    
    shards = list(iter_rule_shards(max_chars=1200, overlap_chars=150))
    
    logger.info(f"Generated {len(shards)} rule shards")
    
    if shards:
        sample = shards[0]
        logger.info(f"Sample shard: mode={sample.mode}, title={sample.title[:50]}...")
        logger.info(f"Content preview: {sample.content[:200]}...")
    
    return len(shards) > 0


def test_rules_indexing():
    """Test indexing rules into Mem0."""
    logger.info("Testing rules indexing into Mem0...")
    
    # Initialize memory service
    mem_cfg = MemoryConfig(
        enable_memory=True,
        vector_store_type="qdrant",
        qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        collection_name="abacus_proxy_memory",
        embedding_model="text-embedding-3-small",
    )
    
    memory_service = MemoryService(mem_cfg)
    
    if not memory_service.is_enabled:
        logger.error("Memory service not enabled - check OPENAI_API_KEY and Qdrant connection")
        return False
    
    # Index rules
    result = index_rules_into_mem0(
        memory_service,
        user_id="system",
        project_id="roo_rules",
        max_chars=1200,
        overlap_chars=150,
        dry_run=False,
        incremental=True,
        prune_removed=True,
    )
    
    logger.info(f"Indexing result: {result}")
    
    return result.get("success", False)


async def test_rules_retrieval():
    """Test retrieving and injecting rules based on a query."""
    logger.info("Testing rules retrieval and injection...")
    
    # Initialize memory service
    mem_cfg = MemoryConfig(
        enable_memory=True,
        vector_store_type="qdrant",
        qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        collection_name="abacus_proxy_memory",
        embedding_model="text-embedding-3-small",
    )
    
    memory_service = MemoryService(mem_cfg)
    
    if not memory_service.is_enabled:
        logger.error("Memory service not enabled")
        return False
    
    # Test query
    test_messages = [
        {"role": "user", "content": "How should I implement a new feature with proper code quality?"}
    ]
    
    # Inject rules
    new_messages, stats = await inject_retrieved_rules(
        messages=test_messages,
        memory_service=memory_service,
        model_id="gpt-4",
        enable=True,
        user_id="system",
        project_id="roo_rules",
        max_shards=5,
        max_tokens=2000,
        trivial_bypass=False,
    )
    
    logger.info(f"Injection stats: {stats}")
    
    if stats.get("injected"):
        logger.info(f"Successfully injected {stats.get('retrieved')} rule shards")
        logger.info(f"Token usage: ~{stats.get('tokens_used')} tokens")
        
        # Show injected content
        if len(new_messages) > len(test_messages):
            injected_msg = new_messages[0]
            logger.info(f"Injected message preview: {injected_msg['content'][:500]}...")
    
    return stats.get("injected", False)


def main():
    """Run all tests."""
    logger.info("=" * 80)
    logger.info("Rules Memory System Integration Test")
    logger.info("=" * 80)
    
    # Test 1: Rule shards generation
    logger.info("\n[TEST 1] Rule Shards Generation")
    test1_pass = test_rule_shards()
    logger.info(f"Result: {'✅ PASS' if test1_pass else '❌ FAIL'}")
    
    # Test 2: Rules indexing
    logger.info("\n[TEST 2] Rules Indexing into Mem0")
    test2_pass = test_rules_indexing()
    logger.info(f"Result: {'✅ PASS' if test2_pass else '❌ FAIL'}")
    
    # Test 3: Rules retrieval
    logger.info("\n[TEST 3] Rules Retrieval and Injection")
    test3_pass = asyncio.run(test_rules_retrieval())
    logger.info(f"Result: {'✅ PASS' if test3_pass else '❌ FAIL'}")
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("Test Summary")
    logger.info("=" * 80)
    logger.info(f"Rule Shards Generation: {'✅ PASS' if test1_pass else '❌ FAIL'}")
    logger.info(f"Rules Indexing: {'✅ PASS' if test2_pass else '❌ FAIL'}")
    logger.info(f"Rules Retrieval: {'✅ PASS' if test3_pass else '❌ FAIL'}")
    
    all_pass = test1_pass and test2_pass and test3_pass
    logger.info(f"\nOverall: {'✅ ALL TESTS PASSED' if all_pass else '❌ SOME TESTS FAILED'}")
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    exit(main())
