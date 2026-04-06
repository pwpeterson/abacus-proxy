# Abacus Proxy

An intelligent proxy for RouteLLM that provides smart model selection, usage tracking, and context management with Mem0 integration.

## Features

### Intelligent Model Routing

When you configure your client with `model: "route-llm"`, abacus-proxy automatically:

1. **Analyzes your request** - Examines message content, tools, and context size
2. **Detects the use case** - Classifies as coding, architecting, or condensation
3. **Scores available models** - Uses value-index system (capability/cost ratio)
4. **Selects the best model** - Chooses optimal model from RouteLLM's catalog
5. **Routes intelligently** - Forwards to the selected model at RouteLLM

**Use Case Detection:**
- **Coding** (default): Implementation tasks, debugging, API work, tool usage
- **Architecting**: System design, planning, strategy, high-level decisions  
- **Condensation**: Large context summarization (>30k characters)

**Model Selection Criteria:**
- Context length requirements
- Reasoning capability (thinking support)
- Coding specialization
- Cost efficiency (value index)
- Maximum output tokens

### Additional Features

- **Context Management**: Mem0-backed conversation condensation and memory
- **Rules Memory**: Semantic retrieval of `.roo` rules for AI agents
- **Code JIT Context**: Semantic code search and injection
- **Tool Registry**: Context-aware tool filtering and injection
- **Prompt Guard**: Automatic message trimming to prevent context overflow
- **Usage Tracking**: Token consumption and cost monitoring

## Prerequisites

- Podman installed
- Abacus.AI API key
- Context Portal and Mem0 accounts/configured

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/abacus-proxy.git
cd abacus-proxy

# Build the container
podman build -t abacus-proxy .

# Run the container
podman run -d \
  --name abacus-proxy \
  -p 8080:8080 \
  -e ABACUS_API_KEY='your_api_key_here' \
  -e CONTEXT_PORTAL_URL='your_context_portal_url' \
  -e MEM0_API_KEY='your_mem0_api_key' \
  abacus-proxy
```

## Configuration

### Core Settings

- `ROUTELLM_API_BASE_URL`: RouteLLM API endpoint (default: `https://routellm.abacus.ai/v1`)
- `ABACUS_API_KEY`: Your Abacus.AI API key for RouteLLM access
- `HOST`: Bind address (default: `0.0.0.0`)
- `PORT`: HTTP port (default: `8003`)

### Intelligent Routing

- `ENABLE_INTELLIGENT_ROUTING`: Enable smart model selection (default: `true`)
- `ENABLE_MODEL_VALUE_INDEX`: Enable value-index scoring (default: `false`)
- `ROUTE_DEFAULT_TARGET_MODEL`: Fallback model when routing disabled (default: `gpt-5.2`)
- `MODEL_ALIASES_JSON`: JSON mapping of model aliases (default: `{"route-llm": "gpt-5.2"}`)

### Memory & Context

- `ENABLE_MEMORY`: Enable Mem0 integration (default: `true`)
- `ENABLE_CONTEXT_MANAGEMENT`: Enable conversation condensation (default: `true`)
- `ENABLE_RULES_MEMORY`: Enable `.roo` rules retrieval (default: `false`)
- `ENABLE_CODE_JIT_CONTEXT`: Enable semantic code search (default: `false`)

### Tool Management

- `ENABLE_TOOL_REGISTRY`: Enable tool schema registry (default: `false`)
- `ENABLE_TOOL_INJECTION`: Server-side tool injection (default: `false`)
- `ENABLE_TOOL_FILTERING`: Client-side tool filtering (default: `false`)

## Usage

### With Roo Code Extension

Configure your Roo provider:

```json
{
  "baseURL": "http://localhost:8003/v1",
  "model": "route-llm"
}
```

abacus-proxy will automatically select the best model for each request!

### Direct API Usage

```bash
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ABACUS_API_KEY" \
  -d '{
    "model": "route-llm",
    "messages": [
      {"role": "user", "content": "Write a Python function to sort a list"}
    ]
  }'
```

**Response includes routing details in logs:**
```
Intelligent routing: use_case=coding selected=qwen/qwen-2.5-coder-32b-instruct 
index=87.3 capability=0.892 cost=$0.000156
```

## API Endpoints

### OpenAI-Compatible

- `POST /v1/chat/completions`: Chat completions (with intelligent routing)
- `POST /v1/completions`: Legacy completions endpoint
- `GET /v1/models`: List available models (optional `?enrich=value_index`)

### Health & Status

- `GET /health`: Health check endpoint

## How It Works

### Request Flow

1. **Client Request** → Roo sends `model: "route-llm"` to abacus-proxy
2. **Use Case Detection** → Analyzes messages, tools, context size
3. **Model Catalog Fetch** → Retrieves available models from RouteLLM (cached)
4. **Value Index Scoring** → Enriches models with capability/cost metrics
5. **Model Selection** → Chooses highest value-index model for use case
6. **Request Forwarding** → Proxies to RouteLLM with selected model
7. **Response Return** → Returns completion to client

### Value Index Calculation

```
value_index = (capability / job_cost) * 100

where:
  capability = weighted_sum(
    context_score * w_ctx,
    reasoning_score * w_reason,
    coding_score * w_coding
  ) * quality_prior * max_out_multiplier
  
  job_cost = (in_tokens * input_rate) + (out_tokens * output_rate)
```

### Use Case Weights

| Use Case | Context | Reasoning | Coding |
|----------|---------|-----------|--------|
| Coding | 20% | 35% | 45% |
| Architecting | 30% | 50% | 20% |
| Condensation | 65% | 25% | 10% |

## Development

```bash
# Install dependencies
uv sync

# Run locally
uv run python -m src.main_fastapi

# Run tests
uv run pytest

# Build container
podman build -t abacus-proxy -f Dockerfile.abacus-proxy .
```