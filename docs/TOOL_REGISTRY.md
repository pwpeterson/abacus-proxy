# Tool Schema Registry

## Overview

The Tool Schema Registry provides context-aware tool filtering and server-side tool schema injection for abacus-proxy. This system intelligently manages which tools are available to the model based on conversation context, reducing payload size and preventing "tool chasing" behavior.

## Features

### 1. Server-Side Tool Injection

When a client omits the `tools` parameter, abacus-proxy can inject a relevant subset of tools from the registry based on conversation context.

**Key Characteristics:**
- **Conservative by design**: Only injects tools when positive evidence of tool intent exists
- **Context-aware**: Analyzes recent messages for keywords and patterns
- **Token-budgeted**: Limits injected tools by token count for stable behavior
- **Prevents tool chasing**: Won't inject tools for generic queries like "What is this app?"

### 2. Client-Side Tool Filtering

When a client provides tools, abacus-proxy can filter them to the most relevant subset based on conversation context.

**Key Characteristics:**
- **Relevance-based**: Scores tools by category match and keyword signals
- **Essential tools preserved**: Always includes configured essential tools
- **Configurable limits**: Respects max_tools configuration

## Configuration

### Environment Variables

```bash
# Enable tool schema registry features
ENABLE_TOOL_REGISTRY=true

# Path to tool schema registry JSON file
TOOL_REGISTRY_PATH=./tool_schema_registry.json

# Server-side tool injection (when client omits tools)
ENABLE_TOOL_INJECTION=true
TOOL_INJECTION_MAX_TOOLS=20
TOOL_INJECTION_MAX_TOKENS=5000

# Client-side tool filtering (when client provides tools)
ENABLE_TOOL_FILTERING=true
TOOL_FILTERING_MAX_TOOLS=50
```

### Registry File Format

The `tool_schema_registry.json` file contains:

```json
{
  "metadata": {
    "version": "1.0.0",
    "description": "Tool schema registry for abacus-proxy",
    "updated_at": "2026-04-06T20:00:00Z"
  },
  "policy": {
    "tool_categories": {
      "file_operations": ["read_file", "write_file", "list_files"],
      "git_operations": ["git_status", "git_commit", "git_push"],
      "code_analysis": ["analyze_codebase", "detect_code_smells"]
    },
    "keyword_signals": {
      "file_operations": ["read", "write", "file", "directory"],
      "git_operations": ["git", "commit", "push", "status"],
      "code_analysis": ["analyze", "review", "refactor"]
    },
    "essential_tools": ["read_file", "write_file"],
    "tool_injection": {
      "enabled": true,
      "max_tools": 20,
      "max_tokens": 5000,
      "conservative_mode": true
    },
    "denylist": {
      "control_tools": ["attempt_completion", "ask_followup_question"]
    }
  },
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read_file",
        "description": "Read file contents",
        "parameters": {
          "type": "object",
          "properties": {
            "path": {"type": "string"}
          },
          "required": ["path"]
        }
      }
    }
  ]
}
```

## How It Works

### Intent Detection

The system detects tool intent by analyzing recent messages for:

1. **Keyword signals**: Configured keywords that indicate tool categories
2. **Tool name mentions**: Explicit references to tool names
3. **File/path patterns**: File extensions, paths, and action verbs
4. **Category patterns**: Patterns that match tool categories

### Conservative Injection Strategy

**Positive Evidence Required:**
- File operations: Requires both file-like tokens (`.py`, `/`, `readme`) AND action verbs (`read`, `open`, `list`)
- Git operations: Requires git-related keywords (`git`, `commit`, `status`)
- Code analysis: Requires analysis keywords (`analyze`, `review`, `refactor`)

**No Injection When:**
- Generic queries: "What is this app?"
- Math/logic: "What is 2 + 2?"
- Explanations: "Explain how X works"
- No tool intent detected in recent messages

### Tool Selection Process

**Stage 1: Intent Gating + Ranking**
1. Extract recent context (last 3 messages)
2. Detect active tool categories
3. Score tools by relevance:
   - Category match: +10 points
   - Tool name mentioned: +20 points
   - Description keywords: +5 points

**Stage 2: Token Budgeting**
1. Sort tools by relevance score
2. Apply token budget (estimate tokens per tool)
3. Select tools until budget exhausted
4. Guarantee at least one tool if any scored positively

## Usage Examples

### Example 1: File Operation Intent

**User Query:**
```
"Can you read the config.json file?"
```

**Detected Intent:**
- Category: `file_operations`
- Keywords: "read", "file"

**Injected Tools:**
- `read_file` (high relevance)
- `list_files` (related)
- `write_file` (related)

### Example 2: Git Operation Intent

**User Query:**
```
"Show me the git status and commit the changes"
```

**Detected Intent:**
- Category: `git_operations`
- Keywords: "git", "status", "commit"

**Injected Tools:**
- `git_status` (explicitly mentioned)
- `git_commit` (explicitly mentioned)
- `git_push` (related)

### Example 3: No Tool Intent

**User Query:**
```
"What is this application used for?"
```

**Detected Intent:**
- None (generic question)

**Injected Tools:**
- None (conservative mode prevents injection)

## Benefits

### 1. Reduced Payload Size
- Only relevant tools included
- Token budget prevents bloat
- Faster request/response cycles

### 2. Improved Model Behavior
- Prevents "tool chasing" on generic queries
- Reduces hallucinated tool calls
- More focused tool usage

### 3. Better Context Management
- More tokens available for actual context
- Reduced risk of context window overflow
- Improved conversation quality

### 4. Flexible Configuration
- Per-category keyword signals
- Adjustable token budgets
- Essential tools always available

## Testing

Run the test suite:

```bash
pytest test/test_tool_registry.py -v
```

Tests cover:
- Registry loading and parsing
- Intent detection (positive and negative cases)
- Tool injection with various intents
- Token budget enforcement
- Client-side tool filtering
- Essential tools preservation

## Troubleshooting

### Tools Not Being Injected

**Check:**
1. `ENABLE_TOOL_REGISTRY=true` in config
2. `ENABLE_TOOL_INJECTION=true` in config
3. Registry file exists at `TOOL_REGISTRY_PATH`
4. Recent messages contain tool intent keywords
5. Check logs for "Injected N tools from registry"

### Too Many/Few Tools Injected

**Adjust:**
- `TOOL_INJECTION_MAX_TOOLS`: Increase/decrease max count
- `TOOL_INJECTION_MAX_TOKENS`: Increase/decrease token budget
- Registry `keyword_signals`: Add/remove keywords for categories

### Wrong Tools Being Injected

**Review:**
- Registry `tool_categories`: Ensure tools are in correct categories
- Registry `keyword_signals`: Ensure keywords match user language
- Recent message context: System analyzes last 3 messages only

## Architecture

### Components

1. **ToolSchemaRegistry** (`src/utils/tool_schema_registry.py`)
   - Registry data structure
   - Loading and validation
   - Tool selection with token budgeting

2. **ToolFilter** (`src/utils/tool_filter.py`)
   - Context-based tool filtering
   - Intent detection
   - Relevance scoring

3. **Integration** (`src/app_fastapi.py`)
   - Request-time tool injection
   - Client tool filtering
   - Logging and monitoring

### Data Flow

```
Request → Extract Messages → Detect Intent → Score Tools → Apply Budget → Inject/Filter → Upstream
```

## Future Enhancements

Potential improvements:
- Machine learning-based intent detection
- Per-user tool preferences
- Dynamic category learning
- Tool usage analytics
- A/B testing framework

## Related Documentation

- [Rules Memory System](./RULES_MEMORY.md)
- [Code JIT Context](./CODE_JIT_CONTEXT.md)
- [Context Management](./CONTEXT_MANAGEMENT.md)