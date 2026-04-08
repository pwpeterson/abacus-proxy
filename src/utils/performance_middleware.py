"""Performance logging middleware for FastAPI.

Tracks request latency, token usage, and other performance metrics.
"""

from __future__ import annotations

import os
import random
import time
from typing import Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class PerformanceLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log request performance metrics."""

    def __init__(self, app, threshold_ms: int = 1000, sampling_rate: int = 1):
        """Initialize performance logging middleware.
        
        Args:
            app: FastAPI application
            threshold_ms: Only log requests slower than this (milliseconds)
            sampling_rate: Sample 1 in N requests (1 = all requests)
        """
        super().__init__(app)
        self.threshold_ms = threshold_ms
        self.sampling_rate = max(1, sampling_rate)
        self.enable_sampling = sampling_rate > 1

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and log performance metrics."""
        # Apply sampling (always log errors)
        should_sample = (
            not self.enable_sampling
            or random.randint(1, self.sampling_rate) == 1
        )

        start_time = time.perf_counter()
        
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            
            # Determine if we should log this request
            is_error = response.status_code >= 400
            is_slow = elapsed_ms >= self.threshold_ms
            should_log = should_sample or is_error or is_slow
            
            if should_log:
                # Extract request context
                request_id = request.headers.get("x-request-id", "unknown")
                user_id = request.headers.get("x-user-id")
                model_id = None
                
                # Try to extract model from request body (best-effort)
                if hasattr(request.state, "model_id"):
                    model_id = request.state.model_id
                
                log_context = {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "is_slow": is_slow,
                    "is_error": is_error,
                }
                
                if user_id:
                    log_context["user_id"] = user_id
                if model_id:
                    log_context["model_id"] = model_id
                
                # Log with appropriate level
                if is_error:
                    logger.bind(**log_context).error(
                        f"Request failed: {request.method} {request.url.path}"
                    )
                elif is_slow:
                    logger.bind(**log_context).warning(
                        f"Slow request: {request.method} {request.url.path}"
                    )
                else:
                    logger.bind(**log_context).info(
                        f"Request completed: {request.method} {request.url.path}"
                    )
            
            return response
            
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            request_id = request.headers.get("x-request-id", "unknown")
            
            logger.bind(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                elapsed_ms=round(elapsed_ms, 2),
                error=str(e),
            ).error(f"Request exception: {request.method} {request.url.path}")
            
            raise


def create_performance_middleware(
    app,
    *,
    enabled: bool = True,
    threshold_ms: int | None = None,
    sampling_rate: int | None = None,
) -> None:
    """Add performance logging middleware to FastAPI app.
    
    Args:
        app: FastAPI application
        enabled: Enable performance logging
        threshold_ms: Only log requests slower than this (from env or default 1000)
        sampling_rate: Sample 1 in N requests (from env or default 1)
    """
    if not enabled:
        logger.info("Performance logging disabled")
        return
    
    # Get configuration from environment
    if threshold_ms is None:
        threshold_ms = int(os.getenv("PERFORMANCE_LOG_THRESHOLD_MS", "1000"))
    
    if sampling_rate is None:
        sampling_rate = int(os.getenv("LOG_SAMPLING_RATE", "1"))
    
    app.add_middleware(
        PerformanceLoggingMiddleware,
        threshold_ms=threshold_ms,
        sampling_rate=sampling_rate,
    )
    
    logger.info(
        f"Performance logging enabled: threshold={threshold_ms}ms, sampling=1/{sampling_rate}"
    )
