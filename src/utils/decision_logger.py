"""Decision logging utilities for tracking architectural and implementation decisions.

This module provides structured logging of development decisions into Mem0,
enabling:
- Historical decision tracking
- Rationale preservation
- Impact analysis
- Decision search and retrieval
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService


class DecisionLogger:
    """Log and track architectural and implementation decisions."""

    def __init__(self, memory_service: MemoryService):
        """Initialize decision logger with memory service."""
        self.memory_service = memory_service

    def log_decision(
        self,
        *,
        title: str,
        description: str,
        rationale: str | None = None,
        alternatives: list[str] | None = None,
        consequences: list[str] | None = None,
        tags: list[str] | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        related_decisions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Log an architectural or implementation decision.

        Args:
            title: Short decision title
            description: Detailed description of the decision
            rationale: Why this decision was made
            alternatives: Alternative approaches considered
            consequences: Expected consequences/impacts
            tags: Categorization tags (e.g., 'architecture', 'security', 'performance')
            user_id: User making the decision
            project_id: Project identifier
            related_decisions: IDs of related decisions

        Returns:
            Dictionary with success status and decision ID
        """
        if not self.memory_service.is_enabled:
            return {"success": False, "message": "Memory service not enabled"}

        timestamp = datetime.now(timezone.utc).isoformat()

        # Format decision as structured text
        decision_text = f"""# Decision: {title}

## Description
{description}

## Rationale
{rationale or 'Not specified'}

## Alternatives Considered
{self._format_list(alternatives) if alternatives else 'None documented'}

## Consequences
{self._format_list(consequences) if consequences else 'None documented'}

## Metadata
- Timestamp: {timestamp}
- Tags: {', '.join(tags) if tags else 'None'}
- Related Decisions: {', '.join(related_decisions) if related_decisions else 'None'}
"""

        metadata = {
            "type": "decision",
            "title": title,
            "timestamp": timestamp,
            "tags": tags or [],
            "related_decisions": related_decisions or [],
            "decision_type": "architectural" if "architecture" in (tags or []) else "implementation",
        }

        result = self.memory_service.upsert_raw_text(
            text=decision_text,
            user_id=user_id,
            metadata=metadata,
        )

        if result.success and result.memory_ids:
            decision_id = result.memory_ids[0]
            logger.info(f"Logged decision: {title} (ID: {decision_id})")
            return {
                "success": True,
                "decision_id": decision_id,
                "message": f"Decision logged: {title}",
            }

        return {"success": False, "message": result.message}

    def search_decisions(
        self,
        *,
        query: str,
        user_id: str | None = None,
        project_id: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for logged decisions.

        Args:
            query: Search query
            user_id: Filter by user
            project_id: Filter by project
            tags: Filter by tags
            limit: Maximum results

        Returns:
            List of matching decisions
        """
        if not self.memory_service.is_enabled:
            return []

        search_result = self.memory_service.search_memory(
            query=query,
            user_id=user_id,
            project_id=project_id,
            limit=limit * 2,  # Over-fetch for filtering
        )

        decisions = []
        for item in search_result.results:
            metadata = item.metadata or {}
            if metadata.get("type") != "decision":
                continue

            # Filter by tags if specified
            if tags:
                item_tags = metadata.get("tags", [])
                if not any(tag in item_tags for tag in tags):
                    continue

            decisions.append(
                {
                    "id": item.id,
                    "title": metadata.get("title", "Untitled"),
                    "content": item.memory,
                    "timestamp": metadata.get("timestamp"),
                    "tags": metadata.get("tags", []),
                    "related_decisions": metadata.get("related_decisions", []),
                    "decision_type": metadata.get("decision_type", "implementation"),
                }
            )

            if len(decisions) >= limit:
                break

        return decisions

    def get_decision_by_id(self, decision_id: str) -> dict[str, Any] | None:
        """Retrieve a specific decision by ID.

        Args:
            decision_id: Decision identifier

        Returns:
            Decision data or None if not found
        """
        # This would require direct vector store access
        # For now, return None (can be enhanced with direct Qdrant query)
        logger.warning("get_decision_by_id not yet implemented")
        return None

    @staticmethod
    def _format_list(items: list[str]) -> str:
        """Format list items as markdown bullet points."""
        if not items:
            return "None"
        return "\n".join(f"- {item}" for item in items)