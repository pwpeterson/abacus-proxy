# Structured Logging Guide

A comprehensive, reusable logging configuration for Python projects using loguru with both human-readable and AI-parseable structured formats.

## Features

✅ **Dual Format Logging**
- Structured JSON logs (`.jsonl`) for AI parsing and analysis
- Human-readable logs (`.log`) for debugging and monitoring
- Rich console output with color coding

✅ **Smart Organization**
- Separate error logs for quick issue identification
- Automatic log rotation (10MB for general, 5MB for errors)
- Configurable retention periods (7 days general, 14 days errors)

✅ **Production Ready**
- Thread-safe logging with queue-based handling
- Full exception tracebacks with local variables
- Process and thread information in structured logs

✅ **Developer Friendly**
- Rich console output with syntax highlighting
- Context-aware logging with extra fields
- Convenience functions for common patterns

## Quick Start

### Basic Setup

```python
from pathlib import Path
from loguru import logger
from src.utils.logging_setup import setup_logging

# Initialize logging
setup_logging(
    log_dir=Path("logs"),
    log_level="INFO",
    enable_rich=True,
    enable_structured=True,
    app_name="my_app"
)

# Start logging
logger.info("Application started")
```

### With Context

```python
# Add context to logs
logger.bind(user_id=123, session="abc-123").info("User logged in")

# Or use convenience functions
from src.utils.logging_setup import log_info, log_error

log_info("Processing request", request_id="req-456", method="POST")
log_error("Connection failed", host="api.example.com", retry_count=3)
```

## Configuration Options

### `setup_logging()` Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `log_dir` | `Path` | `Path("logs")` | Directory for log files |
| `log_level` | `str` | `"INFO"` | Console log level (TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL) |
| `enable_rich` | `bool` | `True` | Enable rich console formatting with colors |
| `enable_structured` | `bool` | `True` | Enable structured JSON logging |
| `app_name` | `str` | `"app"` | Application name for log file naming |

## Log File Structure

After initialization, you'll have these log files:

```
logs/
├── my_app.jsonl              # All logs in JSON format (AI-parseable)
├── my_app_errors.jsonl       # Errors only in JSON format
├── my_app.log                # All logs in human-readable format
└── my_app_errors.log         # Errors only in human-readable format
```

## Structured JSON Format

Each log entry in `.jsonl` files contains:

```json
{
  "timestamp": "2026-01-01T18:37:00.123456",
  "level": "INFO",
  "logger": "my_app.module",
  "module": "module",
  "function": "process_data",
  "line": 42,
  "message": "Processing completed",
  "process": {
    "id": 12345,
    "name": "MainProcess"
  },
  "thread": {
    "id": 67890,
    "name": "MainThread"
  },
  "extra": {
    "user_id": 123,
    "duration_ms": 45.2,
    "status": "success"
  }
}
```

### Exception Logging

When exceptions occur, the JSON includes full traceback:

```json
{
  "timestamp": "2026-01-01T18:37:00.123456",
  "level": "ERROR",
  "message": "Failed to process data",
  "exception": {
    "type": "ValueError",
    "value": "Invalid input format",
    "traceback": "Traceback (most recent call last):\n  File..."
  }
}
```

## Usage Patterns

### 1. Basic Logging

```python
from loguru import logger

logger.trace("Detailed trace information")
logger.debug("Debug information")
logger.info("General information")
logger.success("Operation succeeded")
logger.warning("Warning message")
logger.error("Error occurred")
logger.critical("Critical failure")
```

### 2. Context-Aware Logging

```python
# Bind context for multiple logs
user_logger = logger.bind(user_id=123, session="abc")
user_logger.info("User logged in")
user_logger.info("User viewed profile")
user_logger.info("User logged out")

# One-time context
logger.bind(request_id="req-789").info("Processing request")
```

### 3. Convenience Functions

```python
from src.utils.logging_setup import (
    log_debug,
    log_info,
    log_warning,
    log_error,
    log_critical,
    log_success
)

# Automatically includes context as extra fields
log_info("API call", endpoint="/users", method="GET", status=200)
log_error("Database error", query="SELECT * FROM users", error_code="ER_NO_SUCH_TABLE")
log_success("Task completed", task_id="task-123", duration_ms=456.78)
```

### 4. Exception Logging

```python
try:
    risky_operation()
except Exception as e:
    logger.exception("Operation failed")  # Includes full traceback
```

### 5. Performance Tracking

```python
import time

start = time.time()
process_data()
duration = (time.time() - start) * 1000

logger.bind(duration_ms=duration).info("Data processing completed")
```

## AI Agent Integration

### Parsing Structured Logs

The `.jsonl` files are designed for easy AI parsing:

```python
import json

# Read and parse structured logs
with open("logs/my_app.jsonl", "r") as f:
    for line in f:
        log_entry = json.loads(line)

        # Access structured fields
        timestamp = log_entry["timestamp"]
        level = log_entry["level"]
        message = log_entry["message"]
        extra = log_entry.get("extra", {})

        # Analyze patterns
        if level == "ERROR":
            print(f"Error at {timestamp}: {message}")
```

### Common AI Analysis Tasks

1. **Error Pattern Detection**
```python
errors = [json.loads(line) for line in open("logs/my_app_errors.jsonl")]
error_types = {}
for error in errors:
    exc_type = error.get("exception", {}).get("type", "Unknown")
    error_types[exc_type] = error_types.get(exc_type, 0) + 1
```

2. **Performance Analysis**
```python
logs = [json.loads(line) for line in open("logs/my_app.jsonl")]
durations = [log["extra"]["duration_ms"] for log in logs
             if "duration_ms" in log.get("extra", {})]
avg_duration = sum(durations) / len(durations)
```

3. **User Activity Tracking**
```python
user_logs = [json.loads(line) for line in open("logs/my_app.jsonl")]
user_actions = [log for log in user_logs
                if "user_id" in log.get("extra", {})]
```

## Best Practices

### DO ✅

- **Use structured context**: `logger.bind(key=value).info("message")`
- **Log at appropriate levels**: DEBUG for development, INFO for production events
- **Include relevant context**: user_id, request_id, transaction_id, etc.
- **Use meaningful messages**: "User authentication failed" not "Error in auth"
- **Log exceptions with context**: `logger.exception("Failed to process", user_id=123)`

### DON'T ❌

- **Don't log sensitive data**: passwords, API keys, credit cards, PII
- **Don't log in tight loops**: Use sampling or aggregate metrics
- **Don't use string formatting**: Use `logger.info("User {}", user_id)` not f-strings
- **Don't catch and ignore**: Always log exceptions before handling
- **Don't log everything**: Be selective about what's important

## Migration from Existing Setup

### From Standard Logging

```python
# Old
import logging
logging.basicConfig(level=logging.INFO)
logging.info("Message")

# New
from loguru import logger
from src.utils.logging_setup import setup_logging

setup_logging(log_level="INFO")
logger.info("Message")
```

### From Basic Loguru

```python
# Old
from loguru import logger
logger.add("file.log", rotation="10 MB")

# New
from src.utils.logging_setup import setup_logging
setup_logging(app_name="my_app")  # Handles everything
```

## Reusing Across Projects

### 1. Copy the Module

```bash
# Copy to your project
cp src/utils/logging_setup.py your_project/utils/logging_setup.py
```

### 2. Install Dependencies

```bash
pip install loguru rich
```

### 3. Initialize in Your App

```python
# your_project/main.py
from pathlib import Path
from utils.logging_setup import setup_logging

def main():
    setup_logging(
        log_dir=Path("logs"),
        log_level="INFO",
        app_name="your_app"
    )

    # Your application code here

if __name__ == "__main__":
    main()
```

## Advanced Usage

### Custom Log Levels

```python
from loguru import logger

# Add custom level
logger.level("AUDIT", no=38, color="<blue>")

# Use custom level
logger.log("AUDIT", "User accessed sensitive data", user_id=123)
```

### Multiple Log Destinations

```python
from src.utils.logging_setup import setup_logging

# Setup with custom configuration
setup_logging(app_name="my_app")

# Add additional handler for specific module
logger.add(
    "logs/api_calls.log",
    filter=lambda record: "api" in record["extra"],
    level="DEBUG"
)
```

### Conditional Logging

```python
import os
from loguru import logger

# Enable debug logging in development
log_level = "DEBUG" if os.getenv("ENV") == "development" else "INFO"
setup_logging(log_level=log_level)
```

## Troubleshooting

### Logs Not Appearing

1. Check log level: `setup_logging(log_level="DEBUG")`
2. Verify log directory exists and is writable
3. Check if logger was removed: Don't call `logger.remove()` after setup

### Performance Issues

1. Reduce log level in production: Use INFO or WARNING
2. Disable structured logging if not needed: `enable_structured=False`
3. Use sampling for high-frequency logs

### Rich Console Not Working

1. Install rich: `pip install rich`
2. Check terminal supports colors
3. Fallback to basic console: `enable_rich=False`

## Examples

See the complete examples in [`src/utils/logging_setup.py`](../src/utils/logging_setup.py) `__main__` section.

## Summary

This logging setup provides:
- 📊 **Structured JSON logs** for AI analysis
- 📝 **Human-readable logs** for debugging
- 🎨 **Rich console output** for development
- 🔒 **Thread-safe** and production-ready
- 🚀 **Easy to reuse** across projects

Simply copy [`logging_setup.py`](../src/utils/logging_setup.py) to your project and call `setup_logging()` to get started!
