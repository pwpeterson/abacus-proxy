# Request Tracking Analysis Queries - Quick Reference

These queries help you analyze intelligent routing effectiveness, cost optimization, and context condensation in abacus-proxy.

## Prerequisites

```bash
# Enable request tracking
echo "ENABLE_REQUEST_TRACKING=true" >> .env
podman restart abacus-proxy

# Or run queries via Supabase dashboard SQL editor
# Or use psql:
psql "postgresql://postgres:Sup4b453_1969@localhost:5432/postgres"
```

---

## 🎯 Top 5 Most Useful Queries

### 1. Routing Effectiveness Summary (Last 7 Days)

**What it shows**: How intelligent routing compares to direct routing in terms of cost and performance.

```sql
SELECT 
    routing_method,
    use_case_detected,
    COUNT(*) as total_requests,
    ROUND(AVG(estimated_cost)::numeric, 6) as avg_cost_usd,
    ROUND(SUM(estimated_cost)::numeric, 4) as total_cost_usd,
    AVG(value_index_score) as avg_value_score,
    AVG(latency_ms) as avg_latency_ms,
    ROUND(AVG(request_token_count + response_token_count)) as avg_total_tokens
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '7 days'
GROUP BY routing_method, use_case_detected
ORDER BY total_cost_usd DESC;
```

**Expected output**:
```
 routing_method | use_case_detected | total_requests | avg_cost_usd | total_cost_usd | avg_value_score | avg_latency_ms | avg_total_tokens
----------------+-------------------+----------------+--------------+----------------+-----------------+----------------+------------------
 intelligent    | coding            |            450 |     0.000234 |         0.1053 |            87.3 |           1234 |             2500
 intelligent    | architecting      |            120 |     0.000189 |         0.0227 |            82.1 |           1456 |             1800
 direct         | NULL              |             80 |     0.000312 |         0.0250 |           NULL  |           1123 |             3200
```

**Insights**:
- Intelligent routing saves ~25% on cost per request (0.000234 vs 0.000312)
- Coding use case is most common (450 requests)
- Value scores show good model selection (87.3 for coding)

---

### 2. Context Condensation Savings (Last 7 Days)

**What it shows**: How much Mem0 condensation is saving in tokens and cost.

```sql
SELECT 
    condensation_method,
    COUNT(*) as condensed_requests,
    ROUND(AVG(tokens_saved)) as avg_tokens_saved,
    ROUND(AVG(tokens_saved::float / NULLIF(raw_context_token_count, 0) * 100), 1) as avg_reduction_pct,
    SUM(tokens_saved) as total_tokens_saved,
    -- Estimate cost savings (assuming $0.01 per 1K tokens)
    ROUND((SUM(tokens_saved) / 1000.0 * 0.01)::numeric, 4) as estimated_savings_usd
FROM abacus_proxy_request_contexts
WHERE condensation_method IS NOT NULL
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY condensation_method
ORDER BY total_tokens_saved DESC;
```

**Expected output**:
```
 condensation_method | condensed_requests | avg_tokens_saved | avg_reduction_pct | total_tokens_saved | estimated_savings_usd
---------------------+--------------------+------------------+-------------------+--------------------+-----------------------
 mem0                |                 85 |             3420 |              42.3 |             290700 |                2.9070
```

**Insights**:
- 85 requests benefited from condensation
- Average 42.3% token reduction
- Saved ~$2.91 in 7 days from condensation alone

---

### 3. Model Cost Comparison (Last 30 Days)

**What it shows**: Which models are most/least expensive and their performance.

```sql
SELECT 
    selected_model,
    COUNT(*) as requests,
    ROUND(SUM(estimated_cost)::numeric, 4) as total_cost_usd,
    ROUND(AVG(estimated_cost)::numeric, 6) as avg_cost_per_request,
    AVG(value_index_score) as avg_value_score,
    AVG(latency_ms) as avg_latency_ms,
    ROUND(AVG(request_token_count)) as avg_input_tokens,
    ROUND(AVG(response_token_count)) as avg_output_tokens
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '30 days'
  AND selected_model IS NOT NULL
GROUP BY selected_model
ORDER BY total_cost_usd DESC
LIMIT 10;
```

**Expected output**:
```
 selected_model              | requests | total_cost_usd | avg_cost_per_request | avg_value_score | avg_latency_ms | avg_input_tokens | avg_output_tokens
-----------------------------+----------+----------------+----------------------+-----------------+----------------+------------------+-------------------
 anthropic/claude-3.5-sonnet |      234 |         0.4521 |             0.001932 |            92.1 |           1567 |             1200 |               450
 openai/gpt-4o               |      189 |         0.3214 |             0.001701 |            88.7 |           1234 |             1100 |               380
 meta/llama-3.3-70b          |      421 |         0.1876 |             0.000446 |            85.3 |            987 |             1050 |               420
```

**Insights**:
- Claude 3.5 Sonnet is most expensive but highest value score
- Llama 3.3 70B offers best cost/performance ratio
- Can optimize by routing more requests to Llama for suitable use cases

---

### 4. Hourly Request Volume & Cost Trends (Last 24 Hours)

**What it shows**: Usage patterns and cost distribution throughout the day.

```sql
SELECT 
    DATE_TRUNC('hour', request_timestamp) as hour,
    COUNT(*) as requests,
    ROUND(SUM(estimated_cost)::numeric, 4) as total_cost_usd,
    ROUND(AVG(latency_ms)) as avg_latency_ms,
    COUNT(CASE WHEN routing_method = 'intelligent' THEN 1 END) as intelligent_routes,
    COUNT(CASE WHEN status_code >= 400 THEN 1 END) as errors
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '24 hours'
GROUP BY DATE_TRUNC('hour', request_timestamp)
ORDER BY hour DESC;
```

**Expected output**:
```
 hour                | requests | total_cost_usd | avg_latency_ms | intelligent_routes | errors
---------------------+----------+----------------+----------------+--------------------+--------
 2026-04-06 22:00:00 |       45 |         0.0123 |           1234 |                 38 |      2
 2026-04-06 21:00:00 |       67 |         0.0189 |           1156 |                 59 |      1
 2026-04-06 20:00:00 |       52 |         0.0145 |           1289 |                 47 |      0
```

**Insights**:
- Peak usage at 9 PM (67 requests)
- 88% of requests use intelligent routing (59/67)
- Low error rate (1-2 per hour)

---

### 5. Use Case Performance Breakdown

**What it shows**: How different use cases (coding, architecting, condensation) perform.

```sql
SELECT 
    use_case_detected,
    COUNT(*) as requests,
    ROUND(AVG(request_token_count)) as avg_input_tokens,
    ROUND(AVG(response_token_count)) as avg_output_tokens,
    ROUND(AVG(estimated_cost)::numeric, 6) as avg_cost_usd,
    AVG(value_index_score) as avg_value_score,
    AVG(latency_ms) as avg_latency_ms,
    -- Most common model for this use case
    MODE() WITHIN GROUP (ORDER BY selected_model) as most_common_model
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '7 days'
  AND use_case_detected IS NOT NULL
GROUP BY use_case_detected
ORDER BY requests DESC;
```

**Expected output**:
```
 use_case_detected | requests | avg_input_tokens | avg_output_tokens | avg_cost_usd | avg_value_score | avg_latency_ms | most_common_model
-------------------+----------+------------------+-------------------+--------------+-----------------+----------------+---------------------------
 coding            |      450 |             1200 |               520 |     0.000234 |            87.3 |           1234 | meta/llama-3.3-70b
 architecting      |      120 |              980 |               380 |     0.000189 |            82.1 |           1456 | anthropic/claude-3.5-sonnet
 condensation      |       85 |             8500 |               150 |     0.000512 |            78.9 |           2345 | openai/gpt-4o-mini
```

**Insights**:
- Coding is dominant use case (450 requests)
- Condensation has highest input tokens (8500) as expected
- Different models selected for different use cases (routing working!)

---

## 🔍 Additional Useful Queries

### Error Analysis

```sql
SELECT 
    status_code,
    selected_model,
    COUNT(*) as error_count,
    ROUND(AVG(latency_ms)) as avg_latency_ms,
    array_agg(DISTINCT metadata->>'error_type') as error_types
FROM abacus_proxy_requests
WHERE status_code >= 400
  AND request_timestamp > NOW() - INTERVAL '24 hours'
GROUP BY status_code, selected_model
ORDER BY error_count DESC;
```

### Cost Projection (Next 30 Days)

```sql
WITH daily_costs AS (
    SELECT 
        DATE(request_timestamp) as day,
        SUM(estimated_cost) as daily_cost
    FROM abacus_proxy_requests
    WHERE request_timestamp > NOW() - INTERVAL '7 days'
    GROUP BY DATE(request_timestamp)
)
SELECT 
    ROUND(AVG(daily_cost)::numeric, 4) as avg_daily_cost,
    ROUND((AVG(daily_cost) * 30)::numeric, 2) as projected_monthly_cost
FROM daily_costs;
```

### Top 10 Most Expensive Requests

```sql
SELECT 
    request_id,
    request_timestamp,
    selected_model,
    use_case_detected,
    request_token_count + response_token_count as total_tokens,
    ROUND(estimated_cost::numeric, 6) as cost_usd,
    latency_ms
FROM abacus_proxy_requests
WHERE request_timestamp > NOW() - INTERVAL '7 days'
ORDER BY estimated_cost DESC
LIMIT 10;
```

### Condensation Effectiveness by Use Case

```sql
SELECT 
    r.use_case_detected,
    COUNT(*) as condensed_requests,
    ROUND(AVG(c.tokens_saved)) as avg_tokens_saved,
    ROUND(AVG(c.tokens_saved::float / NULLIF(c.raw_context_token_count, 0) * 100), 1) as avg_reduction_pct
FROM abacus_proxy_request_contexts c
JOIN abacus_proxy_requests r ON c.request_id = r.request_id
WHERE c.condensation_method IS NOT NULL
  AND c.created_at > NOW() - INTERVAL '7 days'
GROUP BY r.use_case_detected
ORDER BY condensed_requests DESC;
```

---

## 📊 Running Queries

### Option 1: Supabase Dashboard

1. Go to https://localhost:3000 (Supabase Studio)
2. Navigate to SQL Editor
3. Paste query and click "Run"

### Option 2: psql Command Line

```bash
# Connect to database
psql "postgresql://postgres:Sup4b453_1969@localhost:5432/postgres"

# Run query
\i query.sql

# Or paste query directly
SELECT * FROM abacus_proxy_requests LIMIT 10;
```

### Option 3: Python Script

```python
import os
import httpx
import json

SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:8100")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

async def run_query(sql: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
            json={"query": sql},
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            }
        )
        return response.json()
```

---

## 🎯 Key Metrics to Monitor

1. **Routing Effectiveness**: Intelligent vs Direct cost comparison
2. **Condensation Savings**: Total tokens saved, % reduction
3. **Cost Trends**: Daily/weekly spending patterns
4. **Error Rates**: Failed requests by model/use case
5. **Model Distribution**: Which models are used most
6. **Use Case Distribution**: What users are doing
7. **Latency**: Response times by model/use case

---

## 💡 Optimization Tips

### Based on Query Results

1. **If intelligent routing shows 20%+ cost savings**:
   - ✅ Keep intelligent routing enabled
   - Consider expanding to more use cases

2. **If condensation saves 30%+ tokens**:
   - ✅ Condensation is working well
   - Consider lowering threshold for more aggressive condensation

3. **If one model dominates cost**:
   - Review if cheaper alternatives exist for that use case
   - Adjust value-index scoring to prefer cost-effective models

4. **If error rate > 5%**:
   - Investigate failing models/use cases
   - Add fallback routing logic

5. **If latency > 2000ms consistently**:
   - Consider faster models for time-sensitive use cases
   - Check if condensation is adding overhead

---

## 📝 Next Steps

1. Run the "Routing Effectiveness Summary" query weekly
2. Monitor "Context Condensation Savings" to validate Mem0 value
3. Use "Model Cost Comparison" to optimize model selection
4. Set up alerts for error rates > 5%
5. Review "Cost Projection" monthly for budget planning

Happy analyzing! 🚀
