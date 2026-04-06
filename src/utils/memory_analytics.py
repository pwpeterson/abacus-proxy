"""Memory analytics and insights.

This module provides analytics and insights about memory usage,
including:
- Usage statistics
- Memory health metrics
- Query performance analysis
- Storage optimization recommendations
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService


class MemoryAnalytics:
    """Analyze memory usage and provide insights."""

    def __init__(self, memory_service: MemoryService):
        """Initialize memory analytics with memory service."""
        self.memory_service = memory_service

    def get_usage_stats(
        self,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Get memory usage statistics.

        Args:
            user_id: Filter by user
            project_id: Filter by project

        Returns:
            Dictionary with usage statistics
        """
        if not self.memory_service.is_enabled:
            return {"enabled": False, "message": "Memory service not enabled"}

        try:
            # Get sample of memories to analyze
            sample_result = self.memory_service.search_memory(
                query="*",  # Match all
                user_id=user_id,
                project_id=project_id,
                limit=1000,
            )

            # Analyze by type
            type_counts: dict[str, int] = {}
            total_size = 0

            for item in sample_result.results:
                metadata = item.metadata or {}
                mem_type = metadata.get("type", "unknown")
                type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
                total_size += len(item.memory or "")

            return {
                "enabled": True,
                "total_memories": sample_result.total,
                "sampled_memories": len(sample_result.results),
                "type_breakdown": type_counts,
                "estimated_total_size_bytes": total_size,
                "average_memory_size_bytes": total_size // len(sample_result.results) if sample_result.results else 0,
                "user_id": user_id,
                "project_id": project_id,
            }
        except Exception as e:
            logger.error(f"Failed to get usage stats: {e}")
            return {"enabled": True, "error": str(e)}

    def get_memory_health(self) -> dict[str, Any]:
        """Get memory system health metrics.

        Returns:
            Dictionary with health metrics
        """
        if not self.memory_service.is_enabled:
            return {
                "status": "disabled",
                "message": "Memory service not enabled",
            }

        try:
            # Check if we can perform basic operations
            test_search = self.memory_service.search_memory(
                query="test",
                limit=1,
            )

            return {
                "status": "healthy",
                "can_search": True,
                "can_write": self.memory_service.is_enabled,
                "backend": self.memory_service.config.vector_store_type,
                "collection": self.memory_service.config.collection_name,
            }
        except Exception as e:
            logger.error(f"Memory health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
            }

    def get_type_distribution(
        self,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Get distribution of memory types.

        Args:
            user_id: Filter by user
            project_id: Filter by project

        Returns:
            Dictionary with type distribution
        """
        if not self.memory_service.is_enabled:
            return {"types": {}, "total": 0}

        try:
            # Sample memories
            sample_result = self.memory_service.search_memory(
                query="*",
                user_id=user_id,
                project_id=project_id,
                limit=500,
            )

            type_counts: dict[str, int] = {}
            for item in sample_result.results:
                metadata = item.metadata or {}
                mem_type = metadata.get("type", "unknown")
                type_counts[mem_type] = type_counts.get(mem_type, 0) + 1

            # Calculate percentages
            total = sum(type_counts.values())
            type_percentages = {
                mem_type: (count / total * 100) if total > 0 else 0
                for mem_type, count in type_counts.items()
            }

            return {
                "types": type_counts,
                "percentages": type_percentages,
                "total": total,
            }
        except Exception as e:
            logger.error(f"Failed to get type distribution: {e}")
            return {"types": {}, "total": 0, "error": str(e)}

    def get_recent_activity(
        self,
        *,
        hours: int = 24,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Get recent memory activity.

        Args:
            hours: Look back this many hours
            user_id: Filter by user
            project_id: Filter by project

        Returns:
            Dictionary with recent activity
        """
        if not self.memory_service.is_enabled:
            return {"recent_memories": [], "count": 0}

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

            # Sample recent memories
            sample_result = self.memory_service.search_memory(
                query="*",
                user_id=user_id,
                project_id=project_id,
                limit=100,
            )

            recent_memories = []
            for item in sample_result.results:
                metadata = item.metadata or {}
                created_at = metadata.get("created_at")

                # Parse timestamp
                try:
                    if created_at:
                        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        if created_dt >= cutoff:
                            recent_memories.append(
                                {
                                    "id": item.id,
                                    "type": metadata.get("type", "unknown"),
                                    "created_at": created_at,
                                    "preview": (item.memory or "")[:100],
                                }
                            )
                except Exception:
                    pass

            return {
                "recent_memories": recent_memories,
                "count": len(recent_memories),
                "hours": hours,
            }
        except Exception as e:
            logger.error(f"Failed to get recent activity: {e}")
            return {"recent_memories": [], "count": 0, "error": str(e)}

    def get_optimization_recommendations(
        self,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recommendations for optimizing memory usage.

        Args:
            user_id: Filter by user
            project_id: Filter by project

        Returns:
            List of recommendations
        """
        if not self.memory_service.is_enabled:
            return []

        recommendations = []

        try:
            stats = self.get_usage_stats(user_id=user_id, project_id=project_id)

            # Check for large memories
            avg_size = stats.get("average_memory_size_bytes", 0)
            if avg_size > 5000:
                recommendations.append(
                    {
                        "type": "size_optimization",
                        "severity": "medium",
                        "message": f"Average memory size is {avg_size} bytes. Consider chunking large memories.",
                        "action": "Review and chunk memories larger than 5KB",
                    }
                )

            # Check type distribution
            type_dist = self.get_type_distribution(user_id=user_id, project_id=project_id)
            types = type_dist.get("types", {})

            if types.get("unknown", 0) > 10:
                recommendations.append(
                    {
                        "type": "metadata_quality",
                        "severity": "low",
                        "message": f"{types['unknown']} memories have unknown type",
                        "action": "Add type metadata to improve searchability",
                    }
                )

            # Check for old memories
            recent = self.get_recent_activity(hours=720, user_id=user_id, project_id=project_id)  # 30 days
            if recent["count"] == 0 and stats.get("total_memories", 0) > 0:
                recommendations.append(
                    {
                        "type": "stale_data",
                        "severity": "low",
                        "message": "No recent memory activity detected",
                        "action": "Consider archiving or cleaning old memories",
                    }
                )

        except Exception as e:
            logger.error(f"Failed to generate recommendations: {e}")

        return recommendations

    def get_search_performance_stats(self) -> dict[str, Any]:
        """Get search performance statistics.

        Returns:
            Dictionary with performance stats
        """
        if not self.memory_service.is_enabled:
            return {"enabled": False}

        try:
            import time

            # Test search performance
            queries = ["test", "code", "error", "decision"]
            timings = []

            for query in queries:
                start = time.time()
                self.memory_service.search_memory(query=query, limit=10)
                elapsed = time.time() - start
                timings.append(elapsed)

            avg_time = sum(timings) / len(timings) if timings else 0

            return {
                "enabled": True,
                "average_search_time_ms": avg_time * 1000,
                "min_search_time_ms": min(timings) * 1000 if timings else 0,
                "max_search_time_ms": max(timings) * 1000 if timings else 0,
                "test_queries": len(queries),
            }
        except Exception as e:
            logger.error(f"Failed to get search performance stats: {e}")
            return {"enabled": True, "error": str(e)}