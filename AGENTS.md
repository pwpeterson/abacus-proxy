# ABACUS-PROXY

An intelligent proxy for RouteLLM that provides smart model selection, usage tracking, and context management with Mem0 integration.

## Architecture

Abacus Proxy acts as a middleware between AI clients (like Roo Code) and RouteLLM. It analyzes incoming requests to determine the optimal model based on use-case, cost, and capability.

### Request Flow
1. **Client Request**: Receives OpenAI-compatible requests (usually specifying `model: "route-llm"`).
2. **Analysis**: Uses a heuristic-based "Use Case Detector" to classify the request (Coding, Architecting, or Condensation).
3. **Model Selection**: 
   - Fetches the available model catalog from RouteLLM.
   - Calculates a "Value Index" for each model based on the detected use case.
   - Selects the best performing/most cost-effective model.
4. **Proxy**: Forwards the modified request to the upstream RouteLLM API.
5. **Enrichment**: Optionally injects memory (Mem0), JIT code context, or rules during the routing phase.

## Tech Stack
- **Framework**: FastAPI / Uvicorn
- **Language**: Python 3.12+
- **Containerization**: Podman / Docker
- **Package Manager**: uv
- **Integration Targets**: RouteLLM (Abacus.AI), Mem0, Context Portal

## Key Components

- `src/main_fastapi.py`: Application entry point and API route definitions.
- `src/mcp/`: Model Context Protocol server implementation for agent tool discovery.
- `src/models/`: Pydantic models for configuration, routing, and tool schemas.
- `src/utils/`: Core logic for model selection, cost calculation, and memory integration.

## Development Commands

### Environment Setup
```bash
uv sync                      # Install dependencies
uv run python -m src.main_fastapi  # Run dev server
```

### Testing
```bash
uv run pytest                # Run test suite
```

### Building
```bash
podman build -t abacus-proxy -f Dockerfile.abacus-proxy .
```

## Configuration (Environment Variables)
- `ABACUS_API_KEY`: Required for RouteLLM access.
- `ENABLE_INTELLIGENT_ROUTING`: Toggles the smart selection logic.
- `ENABLE_MEMORY`: Toggles Mem0 integration.
- `ENABLE_TOOL_REGISTRY`: Toggles the tool injection/filtering system.
