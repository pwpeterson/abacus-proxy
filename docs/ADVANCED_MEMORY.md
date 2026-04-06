# Advanced Memory Features

## Overview

Phase 4 adds advanced memory capabilities to abacus-proxy, including:
- **Decision Logging**: Track architectural and implementation decisions
- **Error Pattern Tracking**: Log and analyze error patterns
- **Enhanced Code Indexing**: Advanced code chunk metadata and analysis
- **Memory Analytics**: Usage statistics and optimization insights

## Features

### 1. Decision Logging

Track architectural and implementation decisions with full context.

**Use Cases:**
- Document why certain approaches were chosen
- Track alternatives considered
- Record expected consequences
- Link related decisions
- Search decision history

**Example:**
```python
from src.utils.decision_logger import DecisionLogger

decision_logger = DecisionLogger(memory_service)

result = decision_logger.log_decision(
    title="Use Qdrant for Vector Storage",
    description="Selected Qdrant as the vector database for semantic search",
    rationale="Qdrant provides better performance and scalability than alternatives",
    alternatives=[
        "Chroma - simpler but less scalable",
        "Pinecone - cloud-only, vendor lock-in",
    ],
    consequences=[
        "Need to run Qdrant service",
        "Better query performance",
        "More complex deployment",
    ],
    tags=["architecture", "database", "vector-search"],
    project_id="abacus-proxy",
)
```

**Search Decisions:**
```python
decisions = decision_logger.search_decisions(
    query="vector database",
    tags=["architecture"],
    limit=10,
)
```

### 2. Error Pattern Tracking

Log errors with context and analyze patterns over time.

**Use Cases:**
- Track recurring errors
- Find solutions to similar errors
- Identify error hotspots
- Analyze error trends
- Document solutions

**Example:**
```python
from src.utils.error_tracker import ErrorTracker

error_tracker = ErrorTracker(memory_service)

# Log an error
try:
    # Some operation
    pass
except Exception as e:
    result = error_tracker.log_error(
        error=e,
        context="During code indexing operation",
        severity="error",
        component="code_indexer",
        solution="Ensure file exists before indexing",
        tags=["indexing", "file-io"],
        project_id="abacus-proxy",
    )
```

**Find Similar Errors:**
```python
similar = error_tracker.search_similar_errors(
    error="FileNotFoundError: config.json",
    limit=5,
)
```

**Analyze Error Patterns:**
```python
patterns = error_tracker.get_error_patterns(
    component="code_indexer",
    severity="error",
    limit=20,
)

print(f"Total errors: {patterns['total_errors']}")
print(f"Unique patterns: {patterns['unique_patterns']}")
for pattern in patterns['patterns']:
    print(f"  {pattern['error_type']}: {pattern['count']} occurrences")
```

### 3. Enhanced Code Indexing

Advanced code indexing with AST parsing and metadata extraction.

**Features:**
- AST-based code parsing (Python)
- Function/class extraction
- Import tracking
- Complexity scoring
- Incremental indexing
- Multi-language support

**Example:**
```python
from src.utils.code_indexer import CodeIndexer

code_indexer = CodeIndexer(memory_service)

# Index a single file
result = code_indexer.index_file(
    file_path="src/utils/memory_service.py",
    project_id="abacus-proxy",
    chunk_size=50,  # Lines per chunk
    overlap=5,      # Overlapping lines
)

print(f"Indexed {result['chunks_indexed']} chunks")
print(f"Functions: {result['structure']['functions']}")
print(f"Classes: {result['structure']['classes']}")
```

**Index Entire Directory:**
```python
result = code_indexer.index_directory(
    directory="src",
    project_id="abacus-proxy",
    patterns=["*.py"],
    exclude_patterns=["*test*", "*__pycache__*"],
    max_files=1000,
)

print(f"Indexed {result['files_indexed']} files")
print(f"Total chunks: {result['total_chunks']}")
```

### 4. Memory Analytics

Comprehensive analytics and insights about memory usage.

**Features:**
- Usage statistics
- Type distribution
- Recent activity tracking
- Health monitoring
- Optimization recommendations
- Search performance metrics

**Example:**
```python
from src.utils.memory_analytics import MemoryAnalytics

analytics = MemoryAnalytics(memory_service)

# Get usage stats
stats = analytics.get_usage_stats(project_id="abacus-proxy")
print(f"Total memories: {stats['total_memories']}")
print(f"Average size: {stats['average_memory_size_bytes']} bytes")
print(f"Type breakdown: {stats['type_breakdown']}")

# Check health
health = analytics.get_memory_health()
print(f"Status: {health['status']}")
print(f"Backend: {health['backend']}")

# Get recommendations
recommendations = analytics.get_optimization_recommendations(
    project_id="abacus-proxy"
)
for rec in recommendations:
    print(f"[{rec['severity']}] {rec['message']}")
    print(f"  Action: {rec['action']}")
```

## MCP Tools

All advanced memory features are exposed via MCP tools:

### Decision Tools
- `log_decision` - Log a decision
- `search_decisions` - Search decisions

### Error Tracking Tools
- `log_error` - Log an error
- `search_similar_errors` - Find similar errors
- `get_error_patterns` - Analyze error patterns

### Code Indexing Tools
- `index_code_file` - Index a single file
- `index_code_directory` - Index a directory

### Analytics Tools
- `get_memory_usage_stats` - Usage statistics
- `get_memory_health` - Health metrics
- `get_memory_type_distribution` - Type distribution
- `get_memory_recent_activity` - Recent activity
- `get_memory_optimization_recommendations` - Optimization tips
- `get_memory_search_performance` - Performance metrics

## Configuration

No additional configuration required - these features use the existing memory service configuration.

Ensure memory is enabled:
```bash
ENABLE_MEMORY=true
MEMORY_VECTOR_STORE=qdrant
QDRANT_URL=http://localhost:6333
```

## Use Cases

### 1. Project Documentation

**Scenario:** Document architectural decisions as they're made

```python
# During development
decision_logger.log_decision(
    title="Implement Tool Schema Registry",
    description="Add context-aware tool filtering and injection",
    rationale="Reduce payload size and prevent tool chasing",
    alternatives=["Static tool lists", "No filtering"],
    consequences=["Better model behavior", "More complex system"],
    tags=["architecture", "tools"],
)
```

### 2. Error Investigation

**Scenario:** Recurring error needs investigation

```python
# When error occurs
error_tracker.log_error(
    error=exception,
    context="During startup indexing",
    component="rules_indexer",
)

# Later, investigate patterns
patterns = error_tracker.get_error_patterns(component="rules_indexer")
similar = error_tracker.search_similar_errors(error=exception)
```

### 3. Code Discovery

**Scenario:** Find relevant code for a feature

```python
# Index codebase once
code_indexer.index_directory(
    directory="src",
    project_id="abacus-proxy",
)

# Search for relevant code
results = memory_service.semantic_code_search(
    SemanticCodeSearchRequest(
        query="tool filtering and injection",
        project_id="abacus-proxy",
        limit=5,
    )
)
```

### 4. System Monitoring

**Scenario:** Monitor memory system health

```python
# Regular health checks
health = analytics.get_memory_health()
if health['status'] != 'healthy':
    logger.warning(f"Memory unhealthy: {health.get('error')}")

# Get optimization recommendations
recommendations = analytics.get_optimization_recommendations()
for rec in recommendations:
    if rec['severity'] == 'high':
        logger.error(f"Critical: {rec['message']}")
```

## Best Practices

### Decision Logging

1. **Log Early**: Document decisions when made, not later
2. **Be Specific**: Include concrete details and context
3. **Link Related**: Reference related decisions
4. **Tag Consistently**: Use consistent tags for searchability
5. **Update When Changed**: Log when decisions are reversed

### Error Tracking

1. **Include Context**: Always provide context about when/where error occurred
2. **Document Solutions**: Add solutions when found
3. **Use Severity Correctly**: Classify errors appropriately
4. **Track Components**: Tag errors by component for analysis
5. **Review Patterns**: Regularly analyze error patterns

### Code Indexing

1. **Index Incrementally**: Re-index only changed files
2. **Exclude Tests**: Exclude test files unless needed
3. **Tune Chunk Size**: Adjust based on code structure
4. **Use Overlap**: Maintain context between chunks
5. **Tag Appropriately**: Use tags for better filtering

### Memory Analytics

1. **Monitor Regularly**: Check health and stats periodically
2. **Act on Recommendations**: Address optimization suggestions
3. **Track Trends**: Monitor usage over time
4. **Clean Old Data**: Archive or remove stale memories
5. **Optimize Queries**: Use analytics to improve search patterns

## Performance Considerations

### Decision Logging
- **Write Cost**: Low (single upsert per decision)
- **Search Cost**: Medium (semantic search)
- **Storage**: ~1-5KB per decision

### Error Tracking
- **Write Cost**: Low (single upsert per error)
- **Pattern Analysis**: Medium (requires aggregation)
- **Storage**: ~2-10KB per error (with stack trace)

### Code Indexing
- **Index Cost**: High (AST parsing + chunking + embedding)
- **Search Cost**: Low (semantic search)
- **Storage**: ~500B-2KB per chunk
- **Recommendation**: Index during off-peak hours

### Memory Analytics
- **Query Cost**: Medium (requires sampling)
- **Computation**: Low (simple aggregations)
- **Caching**: Consider caching stats for frequent queries

## Troubleshooting

### Decision Logging Issues

**Problem:** Decisions not searchable
- **Check:** Ensure memory service is enabled
- **Check:** Verify decisions have proper metadata
- **Solution:** Add more descriptive content and tags

### Error Tracking Issues

**Problem:** Similar errors not found
- **Check:** Error signature generation
- **Check:** Search query matches error message
- **Solution:** Use broader search terms

### Code Indexing Issues

**Problem:** Indexing fails for large files
- **Check:** File size and chunk settings
- **Solution:** Increase chunk size or skip large files

**Problem:** AST parsing errors
- **Check:** File syntax is valid
- **Solution:** Handle parse errors gracefully

### Analytics Issues

**Problem:** Stats show zero memories
- **Check:** Memory service is enabled and initialized
- **Check:** Memories exist in the collection
- **Solution:** Verify Qdrant connection and collection name

## Future Enhancements

Potential improvements:
- Decision impact tracking
- Error prediction based on patterns
- Code quality metrics
- Memory usage forecasting
- Automated cleanup policies
- Cross-project analytics

## Related Documentation

- [Memory Service](./MEMORY_SERVICE.md)
- [Rules Memory System](./RULES_MEMORY.md)
- [Code JIT Context](./CODE_JIT_CONTEXT.md)
- [Context Management](./CONTEXT_MANAGEMENT.md)