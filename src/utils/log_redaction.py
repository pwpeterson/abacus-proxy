"""Utilities for redacting sensitive data from logs.

Provides functions to automatically redact sensitive headers, API keys,
and other patterns from log messages.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


def get_redact_headers() -> set[str]:
    """Get list of headers to redact from environment.
    
    Returns:
        Set of header names (lowercase) to redact
    """
    headers_str = os.getenv(
        "LOG_REDACT_HEADERS",
        "Authorization,X-API-Key,X-Auth-Token,Cookie"
    )
    return {h.strip().lower() for h in headers_str.split(",") if h.strip()}


def get_redact_patterns() -> list[re.Pattern]:
    """Get list of regex patterns to redact from environment.
    
    Returns:
        List of compiled regex patterns
    """
    patterns_str = os.getenv(
        "LOG_REDACT_PATTERNS",
        '["sk-[a-zA-Z0-9]+","Bearer [a-zA-Z0-9]+"]'
    )
    
    try:
        patterns_list = json.loads(patterns_str)
        if not isinstance(patterns_list, list):
            patterns_list = []
    except Exception:
        patterns_list = []
    
    compiled = []
    for pattern in patterns_list:
        try:
            compiled.append(re.compile(pattern))
        except Exception:
            continue
    
    return compiled


def redact_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive headers from a headers dict.
    
    Args:
        headers: Dictionary of HTTP headers
        
    Returns:
        Dictionary with sensitive headers redacted
    """
    redact_list = get_redact_headers()
    redacted = {}
    
    for key, value in headers.items():
        if key.lower() in redact_list:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    
    return redacted


def redact_text(text: str) -> str:
    """Redact sensitive patterns from text.
    
    Args:
        text: Text to redact
        
    Returns:
        Text with sensitive patterns redacted
    """
    patterns = get_redact_patterns()
    
    for pattern in patterns:
        text = pattern.sub("[REDACTED]", text)
    
    return text


def redact_dict(data: dict[str, Any], redact_keys: set[str] | None = None) -> dict[str, Any]:
    """Recursively redact sensitive keys from a dictionary.
    
    Args:
        data: Dictionary to redact
        redact_keys: Set of keys to redact (case-insensitive)
        
    Returns:
        Dictionary with sensitive values redacted
    """
    if redact_keys is None:
        redact_keys = {"authorization", "api_key", "apikey", "token", "secret", "password"}
    
    redacted = {}
    
    for key, value in data.items():
        key_lower = key.lower()
        
        if key_lower in redact_keys:
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = redact_dict(value, redact_keys)
        elif isinstance(value, list):
            redacted[key] = [
                redact_dict(item, redact_keys) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, str):
            redacted[key] = redact_text(value)
        else:
            redacted[key] = value
    
    return redacted


def safe_log_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Prepare a dictionary for safe logging by redacting sensitive data.
    
    Args:
        data: Dictionary to prepare for logging
        
    Returns:
        Dictionary safe for logging
    """
    # First redact known sensitive keys
    redacted = redact_dict(data)
    
    # Then redact any remaining patterns in string values
    return redacted
