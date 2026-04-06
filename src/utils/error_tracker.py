"""Error pattern tracking and analysis.

This module provides structured error logging and pattern detection,
enabling:
- Error history tracking
- Pattern recognition
- Root cause analysis
- Solution tracking
"""

from __future__ import annotations

import hashlib
import traceback
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService


class ErrorTracker:
    """Track and analyze error patterns."""

    def __init__(self, memory_service: MemoryService):
        """Initialize error tracker with memory service."""
        self.memory_service = memory_service

    def log_error(
        self,
        *,
        error: Exception | str,
        context: str | None = None,
        stack_trace: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        tags: list[str] | None = None,
        severity: str = "error",
        component: str | None = None,
        solution: str | None = None,
    ) -> dict[str, Any]:
        """Log an error with context for pattern analysis.

        Args:
            error: Exception object or error message
            context: Additional context about when/where error occurred
            stack_trace: Stack trace (auto-captured if error is Exception)
            user_id: User identifier
            project_id: Project identifier
            tags: Categorization tags
            severity: Error severity (debug, info, warning, error, critical)
            component: Component/module where error occurred
            solution: Known solution or workaround

        Returns:
            Dictionary with success status and error ID
        """
        if not self.memory_service.is_enabled:
            return {"success": False, "message": "Memory service not enabled"}

        timestamp = datetime.now(timezone.utc).isoformat()

        # Extract error details
        if isinstance(error, Exception):
            error_type = type(error).__name__
            error_message = str(error)
            if stack_trace is None:
                stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        else:
            error_type = "GenericError"
            error_message = str(error)
            stack_trace = stack_trace or "No stack trace available"

        # Generate error signature for pattern matching
        error_signature = self._generate_error_signature(error_type, error_message)

        # Format error as structured text
        error_text = f"""# Error: {error_type}

## Message
{error_message}

## Context
{context or 'No additional context'}

## Component
{component or 'Unknown'}

## Stack Trace
```
{stack_trace}
```

## Solution
{solution or 'No solution documented yet'}

## Metadata
- Timestamp: {timestamp}
- Severity: {severity}
- Signature: {error_signature}
- Tags: {', '.join(tags) if tags else 'None'}
"""

        metadata = {
            "type": "error",
            "error_type": error_type,
            "error_signature": error_signature,
            "severity": severity,
            "component": component,
            "timestamp": timestamp,
            "tags": tags or [],
            "has_solution": bool(solution),
        }

        result = self.memory_service.upsert_raw_text(
            text=error_text,
            user_id=user_id,
            metadata=metadata,
        )

        if result.success and result.memory_ids:
            error_id = result.memory_ids[0]
            logger.info(f"Logged error: {error_type} (ID: {error_id}, Signature: {error_signature})")
            return {
                "success": True,
                "error_id": error_id,
                "error_signature": error_signature,
                "message": f"Error logged: {error_type}",
            }

        return {"success": False, "message": result.message}

    def search_similar_errors(
        self,
        *,
        error: Exception | str,
        user_id: str | None = None,
        project_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for similar errors to find patterns and solutions.

        Args:
            error: Exception or error message to search for
            user_id: Filter by user
            project_id: Filter by project
            limit: Maximum results

        Returns:
            List of similar errors with solutions
        """
        if not self.memory_service.is_enabled:
            return []

        # Extract error message for search
        if isinstance(error, Exception):
            error_message = f"{type(error).__name__}: {str(error)}"
        else:
            error_message = str(error)

        search_result = self.memory_service.search_memory(
            query=error_message,
            user_id=user_id,
            project_id=project_id,
            limit=limit * 2,  # Over-fetch for filtering
        )

        similar_errors = []
        for item in search_result.results:
            metadata = item.metadata or {}
            if metadata.get("type") != "error":
                continue

            similar_errors.append(
                {
                    "id": item.id,
                    "error_type": metadata.get("error_type", "Unknown"),
                    "error_signature": metadata.get("error_signature"),
                    "severity": metadata.get("severity", "error"),
                    "component": metadata.get("component"),
                    "timestamp": metadata.get("timestamp"),
                    "has_solution": metadata.get("has_solution", False),
                    "content": item.memory,
                    "tags": metadata.get("tags", []),
                }
            )

            if len(similar_errors) >= limit:
                break

        return similar_errors

    def get_error_patterns(
        self,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
        component: str | None = None,
        severity: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Analyze error patterns and frequency.

        Args:
            user_id: Filter by user
            project_id: Filter by project
            component: Filter by component
            severity: Filter by severity
            limit: Maximum errors to analyze

        Returns:
            Dictionary with pattern analysis
        """
        if not self.memory_service.is_enabled:
            return {"patterns": [], "total_errors": 0}

        # Search for all errors
        search_result = self.memory_service.search_memory(
            query="error exception failure",
            user_id=user_id,
            project_id=project_id,
            limit=limit,
        )

        # Group by signature
        signature_counts: dict[str, dict[str, Any]] = {}
        total_errors = 0

        for item in search_result.results:
            metadata = item.metadata or {}
            if metadata.get("type") != "error":
                continue

            # Apply filters
            if component and metadata.get("component") != component:
                continue
            if severity and metadata.get("severity") != severity:
                continue

            total_errors += 1
            sig = metadata.get("error_signature", "unknown")

            if sig not in signature_counts:
                signature_counts[sig] = {
                    "signature": sig,
                    "error_type": metadata.get("error_type", "Unknown"),
                    "count": 0,
                    "severity": metadata.get("severity", "error"),
                    "component": metadata.get("component"),
                    "has_solution": metadata.get("has_solution", False),
                    "first_seen": metadata.get("timestamp"),
                    "last_seen": metadata.get("timestamp"),
                }

            signature_counts[sig]["count"] += 1
            signature_counts[sig]["last_seen"] = metadata.get("timestamp")

        # Sort by frequency
        patterns = sorted(
            signature_counts.values(),
            key=lambda x: x["count"],
            reverse=True,
        )

        return {
            "patterns": patterns,
            "total_errors": total_errors,
            "unique_patterns": len(patterns),
        }

    @staticmethod
    def _generate_error_signature(error_type: str, error_message: str) -> str:
        """Generate a stable signature for error pattern matching.

        Args:
            error_type: Type of error
            error_message: Error message

        Returns:
            8-character hex signature
        """
        # Normalize message (remove numbers, paths, etc.)
        normalized = error_message.lower()
        # Remove common variable parts
        for pattern in [r"\d+", r"/[^\s]+", r"0x[0-9a-f]+", r"line \d+"]:
            import re
            normalized = re.sub(pattern, "", normalized)

        # Generate signature
        signature_input = f"{error_type}:{normalized}".encode("utf-8")
        return hashlib.sha256(signature_input).hexdigest()[:8]