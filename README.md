# Abacus Proxy

An intelligent proxy for RouteLLM that optimizes model selection, tracks usage, and manages context with Context Portal and Mem0.

## Features

- **Smart Model Selection**: Automatically chooses the optimal RouteLLM model based on context and chat history.
- **Usage Tracking**: Monitors token consumption and associated costs for each request.
- **Context Management**: Integrates with Context Portal and Mem0 for persistent conversation memory.
- **Podman Containerized**: Runs as a lightweight, secure container using Podman.

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

Environment variables:

- `ABACUS_API_KEY`: Your Abacus.AI API key for RouteLLM access
- `CONTEXT_PORTAL_URL`: URL for your Context Portal instance
- `MEM0_API_KEY`: API key for Mem0 integration
- `PORT` (optional): Port to run the proxy on (default: 8080)

## Usage

Once running, the proxy will be available at `http://localhost:8080`.

### Example Request

```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Explain quantum computing"}
    ]
  }'
```

## API Endpoints

- `POST /chat`: Submit a chat conversation for processing
- `GET /usage`: Retrieve token usage and cost statistics
- `GET /models`: List available RouteLLM models

## Architecture

1. Receives requests through REST API
2. Analyzes context and history using Context Portal/Mem0
3. Selects optimal RouteLLM model
4. Processes request with RouteLLM API
5. Tracks token usage and costs
6. Returns response to client

> Note: This is a conceptual README for your planned abacus-proxy application. Implementation details will depend on your specific requirements and chosen technologies for the proxy logic.