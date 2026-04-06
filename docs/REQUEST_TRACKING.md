# Request Tracking Implementation Guide

## Overview

Abacus-proxy now includes comprehensive request/response tracking to Supabase for analyzing:
- **Intelligent routing effectiveness** (value-index scores, use case detection)
- **Context condensation effectiveness** (tokens saved via Mem0)
- **Cost optimization and trends** (estimated costs per request)
- **Request payload transformations** (debugging)

## Implementation Status

### ✅ Completed

1. **Database Schema** (`supabase/migrations/20260406_create_abacus_proxy_request_tracking.sql`)
   - `abacus_proxy_requests` table with routing intelligence fields
   - `abacus_proxy_request_contexts` table for condensation analysis
   - Indexes for efficient querying
   - Row-level security policies

2. **Request Logger** (`src/utils/request_logger.py`)
   - `AbacusRequestLogger` class (adapted from model-proxy's `ProxyApiLogger`)
   - Token counting via tiktoken
   - Cost estimation
   - Best-effort telemetry (never blocks requests)
   - Privacy-aware payload storage

3. **Configuration** (`src/models/config.py`)
   - `enable_request_tracking` - Master switch (default: False)
   - `supabase_url` / `supabase_service_role_key` - Connection config
   - `store_request_payloads` - Privacy control (default: False)
   - `store_response_payloads` - Privacy control (default: False)
   - `store_context_text` - Privacy control (default: False)
   - `request_tracking_timeout_seconds` - HTTP timeout (default: 2.5s)

### 🚧 Remaining Work

4. **Integration in `src/app_fastapi.py`**
   - Initialize `AbacusRequestLogger` in lifespan startup
   - Add to `app.state.request_logger`
   - Call `log_request()` after successful/failed requests
   - Capture routing intelligence metadata
   - Track context condensation stats

5. **Environment Configuration** (`abacus-proxy.env`)
   - Add Supabase connection variables
   - Document privacy controls

6. **README Documentation**
   - Usage guide
   - Analysis query examples
   - Privacy considerations

## Database Schema

### `abacus_proxy_requests`

Main request log with routing intelligence:

```sql
CREATE TABLE abacus_proxy_requests (
    id UUID PRIMARY KEY,
    request_id TEXT UNIQUE NOT NULL,
    
    -- Dimensions
    app_id VARCHAR(255),
    provider VARCHAR(100) DEFAULT 'routellm',
    model VARCHAR(255) NOT NULL,
    
    -- Timing
    request_timestamp TIMESTAMPTZ,
    response_timestamp TIMESTAMPTZ,
    latency_ms INTEGER,
    status_code INTEGER,
    
    -- Tokens & Cost
    request_token_count INTEGER,
    response_token_count INTEGER,
    estimated_cost NUMERIC(10,6),
    
    -- Intelligent Routing (NEW)
    routing_method VARCHAR(50),      -- 'intelligent', 'direct', 'fallback'
    requested_model VARCHAR(255),    -- What client asked for
    selected_model VARCHAR(255),     -- What we actually used
    value_index_score INTEGER,       -- 0-100 capability/cost ratio
    use_case_detected VARCHAR(50),   -- 'coding', 'architecting', 'condensation'
    
    -- Payloads (optional)
    request_payload JSONB,
    response_payload JSONB,
    
    metadata JSONB DEFAULT '{}'
);
```

### `abacus_proxy_request_contexts`

Context condensation analysis:

```sql
CREATE TABLE abacus_proxy_request_contexts (
    id UUID PRIMARY KEY,
    request_id TEXT REFERENCES abacus_proxy_requests(request_id),
    
    -- Context text (optional)
    raw_request_context TEXT,
    condensed_request_context TEXT,
    
    -- Provenance
    condensation_method VARCHAR(100),   -- 'mem0', 'rules_memory', etc.
    condensation_version VARCHAR(50),
    condensation_trigger VARCHAR(100),  -- 'threshold', 'manual', 'auto'
    
    -- Metrics
    raw_context_chars INTEGER,
    condensed_context_chars INTEGER,
    raw_context_token_count INTEGER,
    condensed_context_token_count INTEGER,
    
    -- Computed savings
    tokens_saved INTEGER GENERATED ALWAYS AS (
        COALESCE(raw_context_token_count, 0) - 
        COALESCE(condensed_context_token_count, 0)
    ) STORED
);
```

## Integration Steps

### Step 1: Run Supabase Migration

```bash
cd /home/pwp/srv/apps/abacus-proxy
supabase db push
```

Or manually via Supabase dashboard:
```bash
cat supabase/migrations/20260406_create_abacus_proxy_request_tracking.sql | \
  psql "postgresql://postgres:[password]@[host]:5432/postgres"
```

### Step 2: Initialize Logger in `app_fastapi.py`

Add to lifespan startup (after memory service initialization):

```python
from src.utils.request_logger import AbacusRequestLogger

# In lifespan startup:
request_logger = AbacusRequestLogger(
    enabled=bool(getattr(config, "enable_request_tracking", False)),
    supabase_url=getattr(config, "supabase_url", None),
    supabase_service_role_key=getattr(config, "supabase_service_role_key", None),
    http_timeout_seconds=float(
        getattr(config, "request_tracking_timeout_seconds", 2.5)
    ),
)
app.state.request_logger = request_logger
logger.info(f"Request tracking: enabled={request_logger.enabled}")
```

### Step 3: Log Requests After Processing

In the chat completions endpoint (after successful/failed request):

```python
# After processing request
try:
    request_logger: AbacusRequestLogger | None = getattr(
        app.state, "request_logger", None
    )
    if request_logger and request_logger.enabled:
        # Determine routing metadata
        routing_method = "intelligent" if intelligent_routing_used else "direct"
        
        # Build context JSON
        raw_context_json = json.dumps(
            messages, ensure_ascii=False, sort_keys=True, default=str
        )
        
        # Log request
        request_logger.log_request(
            request_id=request_id,
            request_payload=outbound_payload if config.store_request_payloads else {},
            response_payload=response if config.store_response_payloads else {},
            status_code=200,
            app_id=app_id,
            request_time=request_start_time,
            response_time=datetime.now(timezone.utc),
            duration_ms=latency_ms,
            provider_name="routellm",
            model=model_id,
            prompt_char_count=len(raw_context_json),
            # Routing intelligence
            routing_method=routing_method,
            requested_model=requested_model,
            selected_model=selected_model,
            value_index_score=value_index_score,
            use_case_detected=use_case,
            # Context tracking
            raw_request_context=raw_context_json if config.store_context_text else None,
            condensed_request_context=raw_context_json if config.store_context_text else None,
            condensation_method="mem0" if condensed else None,
            condensation_version="1.0",
            condensation_trigger="threshold" if condensed else None,
            raw_context_token_count=initial_token_count,
            condensed_context_token_count=final_token_count,
            metadata={
                "rules_injected": rules_stats.get("injected"),
                "code_jit_injected": code_jit_stats.get("injected"),
            },
        )
except Exception as e:
    logger.warning(f"Request tracking failed (ignored): {e}")
```

### Step 4: Configure Environment

Add to `abacus-proxy.env`:

```bash
# Request Tracking (Supabase telemetry)
# Logs API requests/responses for analyzing routing effectiveness and cost optimization
ENABLE_REQUEST_TRACKING=false  # Set to true to enable

# Supabase connection (required if tracking enabled)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# Privacy controls (all default to false for privacy)
# WARNING: Enabling these stores sensitive data in Supabase
STORE_REQUEST_PAYLOADS=false   # Store full request payloads
STORE_RESPONSE_PAYLOADS=false  # Store full response payloads
STORE_CONTEXT_TEXT=false       # Store raw/condensed context text

# HTTP timeout for telemetry writes (never blocks requests)
REQUEST_TRACKING_TIMEOUT_SECONDS=2.5
```

## Analysis Queries

### Routing Effectiveness

```sql
-- How often does intelligent routing save money?
SELECT 
    routing_method,
    use_case_detected,
    COUNT(*) as requests,
    AVG(estimated_cost) as avg_cost,
    AVG(value_index_score) as avg_value_score,
    SUM(estimated_cost) as total_cost
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '7 days'
GROUP BY routing_method, use_case_detected
ORDER BY total_cost DESC;
```

### Context Condensation Effectiveness

```sql
-- How much are we saving via condensation?
SELECT 
    condensation_method,
    COUNT(*) as requests,
    AVG(tokens_saved) as avg_tokens_saved,
    AVG(tokens_saved::float / NULLIF(raw_context_token_count, 0) * 100) as avg_reduction_pct,
    SUM(tokens_saved) as total_tokens_saved
FROM abacus_proxy_request_contexts
WHERE condensation_method IS NOT NULL
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY condensation_method;
```

### Cost Analysis by Model

```sql
-- Which models are most expensive?
SELECT 
    model,
    COUNT(*) as requests,
    SUM(estimated_cost) as total_cost,
    AVG(estimated_cost) as avg_cost,
    AVG(latency_ms) as avg_latency_ms,
    AVG(value_index_score) as avg_value_score
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '30 days'
GROUP BY model
ORDER BY total_cost DESC
LIMIT 20;
```

### Error Analysis

```sql
-- What's failing and why?
SELECT 
    status_code,
    model,
    COUNT(*) as error_count,
    array_agg(DISTINCT error_message) as error_messages
FROM abacus_proxy_requests
WHERE status_code >= 400
  AND request_timestamp > NOW() - INTERVAL '24 hours'
GROUP BY status_code, model
ORDER BY error_count DESC;
```

### Use Case Distribution

```sql
-- What are users doing?
SELECT 
    use_case_detected,
    COUNT(*) as requests,
    AVG(request_token_count) as avg_input_tokens,
    AVG(response_token_count) as avg_output_tokens,
    AVG(estimated_cost) as avg_cost
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '7 days'
  AND use_case_detected IS NOT NULL
GROUP BY use_case_detected
ORDER BY requests DESC;
```

## Privacy Considerations

### Default Privacy-Safe Configuration

By default, request tracking is **disabled** and when enabled, **does NOT store**:
- Request payloads (messages, tools, parameters)
- Response payloads (assistant messages, tool calls)
- Raw/condensed context text

### What IS Stored (Privacy-Safe)

- Request metadata (timestamps, latency, status codes)
- Token counts (input/output)
- Estimated costs
- Routing decisions (method, model selection, value scores)
- Condensation metrics (tokens saved, method used)
- Character counts (for analysis)

### Enabling Payload Storage

⚠️ **WARNING**: Only enable payload storage if:
1. You have explicit user consent
2. You comply with data privacy regulations (GDPR, CCPA, etc.)
3. You have proper data retention and deletion policies
4. Your Supabase instance has appropriate security controls

```bash
# Enable with caution
STORE_REQUEST_PAYLOADS=true
STORE_RESPONSE_PAYLOADS=true
STORE_CONTEXT_TEXT=true
```

## Benefits

### 1. Routing Optimization
- Measure intelligent routing cost savings
- Identify which use cases benefit most from routing
- Optimize value-index scoring parameters

### 2. Context Management
- Quantify condensation effectiveness
- Track tokens saved over time
- Identify opportunities for improvement

### 3. Cost Control
- Monitor spending by model and use case
- Identify cost outliers
- Project future costs based on trends

### 4. Debugging
- Analyze request/response transformations
- Identify error patterns
- Troubleshoot routing decisions

## Next Steps

1. ✅ Run Supabase migration
2. ✅ Add logger initialization to `app_fastapi.py`
3. ✅ Integrate logging calls in request handlers
4. ✅ Update `abacus-proxy.env` with configuration
5. ✅ Test with `ENABLE_REQUEST_TRACKING=true`
6. ✅ Run analysis queries to verify data
7. ✅ Document findings and optimize routing

## Troubleshooting

### Logger Not Initializing

Check logs for:
```
Request tracking enabled but Supabase is not configured (missing url/key)
```

Solution: Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in environment.

### No Data in Tables

Check:
1. `ENABLE_REQUEST_TRACKING=true` in config
2. Logger initialized: `app.state.request_logger.enabled == True`
3. Supabase connection working (check logs for insert errors)
4. RLS policies allow service role writes

### High Latency

Request tracking uses best-effort async writes with a 2.5s timeout. If Supabase is slow:
1. Increase `REQUEST_TRACKING_TIMEOUT_SECONDS`
2. Check Supabase instance performance
3. Consider disabling tracking temporarily

## References

- Model-proxy implementation: `/home/pwp/srv/apps/model-proxy/src/utils/proxy_api_logger.py`
- Supabase schema: `supabase/migrations/20260406_create_abacus_proxy_request_tracking.sql`
- Configuration: `src/models/config.py`
- Logger: `src/utils/request_logger.py`
