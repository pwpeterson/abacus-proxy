"""Context management (conversation condensation) using Mem0.

Ported/adapted from model-proxy's ContextManager to abacus-proxy.

Behavior:
- Estimate token usage for the message list
- If usage exceeds a threshold fraction of model context, "condense":
  - Keep a tail slice of recent messages
  - Store older messages into Mem0 (best-effort)
  - Retrieve semantically relevant context from Mem0 and inject as a system message
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService
from src.utils.token_estimator import estimate_messages_tokens


class ContextManager:
    """Manages context size using Mem0 for intelligent condensation.
    
    Enhanced with:
    - Dynamic threshold adjustment based on request characteristics
    - Message importance scoring for better retention decisions
    - System message compression to prevent bloat
    - Adaptive summary retrieval based on available budget
    """

    def __init__(
        self,
        memory_service: MemoryService,
        *,
        context_threshold: float = 0.7,
        keep_recent_ratio: float = 0.2,
        min_keep_messages: int = 5,
        summary_limit: int = 3,
        enable_dynamic_threshold: bool = True,
        enable_importance_scoring: bool = True,
        enable_system_compression: bool = True,
        enable_adaptive_summary: bool = True,
    ) -> None:
        self.memory_service = memory_service
        self.context_threshold = float(context_threshold)
        self.keep_recent_ratio = float(keep_recent_ratio)
        self.min_keep_messages = int(min_keep_messages)
        self.summary_limit = int(summary_limit)
        self.enable_dynamic_threshold = bool(enable_dynamic_threshold)
        self.enable_importance_scoring = bool(enable_importance_scoring)
        self.enable_system_compression = bool(enable_system_compression)
        self.enable_adaptive_summary = bool(enable_adaptive_summary)

    def _calculate_dynamic_threshold(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        base_threshold: float | None = None,
    ) -> float:
        """Calculate dynamic threshold based on request characteristics.
        
        Args:
            messages: Chat messages to analyze
            tools: Optional tool schemas
            base_threshold: Override base threshold (default: self.context_threshold)
            
        Returns:
            Adjusted threshold (0.0-1.0)
        """
        if not self.enable_dynamic_threshold:
            return base_threshold or self.context_threshold
        
        threshold = base_threshold or self.context_threshold
        
        # Calculate total input size
        total_chars = sum(
            len(str(m.get("content", "")))
            for m in messages
            if isinstance(m, dict)
        )
        
        # Large context = condensation use case (trigger earlier)
        if total_chars > 30000:
            return min(threshold, 0.5)
        
        # Check for coding indicators
        has_tools = tools and isinstance(tools, list) and len(tools) > 0
        
        # Count code blocks and technical keywords
        content_lower = " ".join(
            str(m.get("content", "")).lower()
            for m in messages
            if isinstance(m, dict)
        )
        
        coding_keywords = [
            "code", "implement", "function", "class", "debug", "refactor",
            "bug", "error", "fix", "test", "api", "endpoint",
        ]
        coding_score = sum(1 for kw in coding_keywords if kw in content_lower)
        
        # Coding tasks with tools need buffer for responses
        if has_tools and coding_score >= 3:
            return min(threshold, 0.6)
        
        # Architecture/planning keywords
        arch_keywords = [
            "design", "architecture", "plan", "strategy", "approach",
            "system", "component", "workflow",
        ]
        arch_score = sum(1 for kw in arch_keywords if kw in content_lower)
        
        # Architecture tasks can use more context
        if arch_score >= 2:
            return min(threshold, 0.75)
        
        return threshold

    def _score_message_importance(
        self,
        msg: dict[str, Any],
        position: int,
        total: int,
    ) -> float:
        """Score message importance for retention decisions.
        
        Args:
            msg: Message to score
            position: Position in message list (0-based)
            total: Total number of messages
            
        Returns:
            Importance score (higher = more important)
        """
        if not self.enable_importance_scoring:
            return 1.0
        
        score = 1.0
        
        # Recency bonus (exponential decay)
        recency = (total - position) / max(total, 1)
        score *= (1.0 + recency * 2.0)
        
        # Tool call/result bonus (critical for context)
        if msg.get("tool_calls") or msg.get("tool_call_id"):
            score *= 2.5
        
        # Error/warning bonus
        content = str(msg.get("content", "")).lower()
        if any(kw in content for kw in ["error", "failed", "warning", "exception"]):
            score *= 1.8
        
        # Code block bonus
        if "```" in str(msg.get("content", "")):
            score *= 1.4
        
        # Question bonus (user questions are important)
        if msg.get("role") == "user" and "?" in content:
            score *= 1.3
        
        return score

    def _compress_system_messages(
        self,
        system_msgs: list[dict[str, Any]],
        max_tokens: int,
        model_id: str,
    ) -> list[dict[str, Any]]:
        """Compress system messages when they exceed budget.
        
        Strategy:
        1. Deduplicate identical content
        2. Merge similar sections
        3. Truncate if still over budget
        
        Args:
            system_msgs: System messages to compress
            max_tokens: Maximum allowed tokens
            model_id: Model ID for token estimation
            
        Returns:
            Compressed system messages
        """
        if not self.enable_system_compression:
            return system_msgs
        
        if not system_msgs:
            return []
        
        current_tokens = estimate_messages_tokens(system_msgs, model_id)
        
        if current_tokens <= max_tokens:
            return system_msgs
        
        logger.info(
            f"System messages exceed budget: {current_tokens}/{max_tokens} tokens. Compressing..."
        )
        
        # Step 1: Deduplicate by content hash
        compressed = []
        seen_hashes = set()
        
        for msg in system_msgs:
            content = msg.get("content", "")
            if not isinstance(content, str):
                compressed.append(msg)
                continue
            
            # Hash the content
            content_hash = hashlib.md5(content.encode()).hexdigest()
            
            if content_hash in seen_hashes:
                logger.debug(f"Deduplicating system message (hash: {content_hash[:8]})")
                continue
            
            seen_hashes.add(content_hash)
            compressed.append(msg)
        
        # Step 2: Check if deduplication was enough
        current_tokens = estimate_messages_tokens(compressed, model_id)
        
        if current_tokens <= max_tokens:
            logger.info(
                f"System message deduplication successful: {len(system_msgs)} → {len(compressed)} messages"
            )
            return compressed
        
        # Step 3: Truncate proportionally if still over budget
        per_message_budget = max_tokens // max(len(compressed), 1)
        
        for msg in compressed:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            
            msg_tokens = estimate_messages_tokens([msg], model_id)
            
            if msg_tokens > per_message_budget:
                # Truncate to budget (keep head for system messages)
                chars_to_keep = int(len(content) * (per_message_budget / max(msg_tokens, 1)))
                msg["content"] = content[:chars_to_keep] + "\n[...truncated for space...]"
        
        final_tokens = estimate_messages_tokens(compressed, model_id)
        logger.info(
            f"System message compression complete: {current_tokens} → {final_tokens} tokens"
        )
        
        return compressed

    def _calculate_adaptive_summary_limit(
        self,
        tokens_saved: int,
        max_context: int,
    ) -> int:
        """Calculate adaptive summary retrieval limit based on available budget.
        
        Args:
            tokens_saved: Tokens saved by condensation
            max_context: Maximum context length
            
        Returns:
            Number of summary items to retrieve
        """
        if not self.enable_adaptive_summary:
            return self.summary_limit
        
        # Calculate savings ratio
        savings_ratio = tokens_saved / max(max_context, 1)
        
        # Scale summary limit based on available budget
        if savings_ratio > 0.5:
            return 8  # Lots of room
        elif savings_ratio > 0.3:
            return 5  # Moderate room
        elif savings_ratio > 0.15:
            return 4  # Some room
        else:
            return max(2, self.summary_limit)  # Tight budget

    def manage_context(
        self,
        *,
        messages: list[dict[str, Any]],
        model_id: str,
        max_context_length: int,
        user_id: str | None = None,
        session_id: str | None = None,
        enable_condensation: bool = True,
        project_id: str | None = None,
        agent_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Condense a conversation when it is approaching the model's context limit.

        Enhanced features:
        - Dynamic threshold adjustment based on request characteristics
        - Message importance scoring for better retention decisions
        - System message compression to prevent bloat
        - Adaptive summary retrieval based on available budget
        - Preserve ALL existing system messages (don't store them in Mem0)
        - Condense only non-system history into Mem0 and keep a tail slice
        - Include tool-schema tokens in the threshold calculation when provided
        - Insert the retrieved summary AFTER the existing system messages
        """
        current_tokens = estimate_messages_tokens(messages, model_id, tools=tools, use_cache=True)
        
        # Use dynamic threshold if enabled
        dynamic_threshold = self._calculate_dynamic_threshold(
            messages=messages,
            tools=tools,
        )
        threshold_tokens = int(max_context_length * dynamic_threshold)

        stats: dict[str, Any] = {
            "original_tokens": current_tokens,
            "original_message_count": len(messages),
            "threshold_tokens": threshold_tokens,
            "max_context_length": max_context_length,
            "dynamic_threshold": dynamic_threshold,
            "base_threshold": self.context_threshold,
            "utilization_percent": (current_tokens / max_context_length) * 100
            if max_context_length > 0
            else 0.0,
            "condensed": False,
            "messages_stored": 0,
            "messages_kept": 0,
            "system_messages_kept": 0,
            "final_tokens": current_tokens,
            "tokens_saved": 0,
            "savings_percent": 0.0,
            "summary_added": False,
            "importance_scoring_enabled": self.enable_importance_scoring,
            "system_compression_enabled": self.enable_system_compression,
        }

        if not enable_condensation or current_tokens <= threshold_tokens:
            return messages, stats

        if not self.memory_service.is_enabled:
            stats["condensation_error"] = "memory_disabled"
            return messages, stats

        system_messages: list[dict[str, Any]] = []
        non_system_messages: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                system_messages.append(m)
            else:
                non_system_messages.append(m)

        # Compress system messages if they're too large
        if self.enable_system_compression and system_messages:
            # Allow system messages up to 30% of context
            system_budget = int(max_context_length * 0.3)
            system_messages = self._compress_system_messages(
                system_messages,
                max_tokens=system_budget,
                model_id=model_id,
            )

        stats["system_messages_kept"] = len(system_messages)

        # Use importance scoring to determine which messages to keep
        if self.enable_importance_scoring and non_system_messages:
            # Score all non-system messages
            scored_messages = [
                (
                    msg,
                    self._score_message_importance(msg, idx, len(non_system_messages)),
                )
                for idx, msg in enumerate(non_system_messages)
            ]
            
            # Sort by importance (descending)
            scored_messages.sort(key=lambda x: x[1], reverse=True)
            
            # Calculate how many to keep
            keep_non_system_count = max(
                self.min_keep_messages,
                int(len(non_system_messages) * self.keep_recent_ratio),
            )
            if keep_non_system_count >= len(non_system_messages):
                keep_non_system_count = max(1, len(non_system_messages) - 1)
            
            # Keep top-scored messages
            messages_to_keep = [msg for msg, score in scored_messages[:keep_non_system_count]]
            messages_to_store = [msg for msg, score in scored_messages[keep_non_system_count:]]
            
            # Restore chronological order for kept messages
            recent_non_system = sorted(
                messages_to_keep,
                key=lambda m: non_system_messages.index(m),
            )
            old_non_system = sorted(
                messages_to_store,
                key=lambda m: non_system_messages.index(m),
            )
        else:
            # Original tail-based retention
            keep_non_system_count = max(
                self.min_keep_messages,
                int(len(non_system_messages) * self.keep_recent_ratio),
            )
            if keep_non_system_count >= len(non_system_messages):
                keep_non_system_count = max(1, len(non_system_messages) - 1)

            recent_non_system = non_system_messages[-keep_non_system_count:]
            old_non_system = non_system_messages[:-keep_non_system_count]

        stats["messages_kept"] = len(system_messages) + len(recent_non_system)
        stats["messages_stored"] = len(old_non_system)

        logger.info(
            "Context threshold exceeded: "
            f"{current_tokens}/{threshold_tokens} tokens "
            f"({stats['utilization_percent']:.1f}% of {max_context_length}). "
            "Condensing using Mem0..."
        )

        condensed_messages: list[dict[str, Any]] = list(system_messages) + list(recent_non_system)

        if not old_non_system:
            # Nothing to store/condense beyond system+tail.
            new_tokens = estimate_messages_tokens(condensed_messages, model_id, tools=tools)
            stats["final_tokens"] = new_tokens
            stats["tokens_saved"] = max(0, current_tokens - new_tokens)
            stats["savings_percent"] = (
                (stats["tokens_saved"] / current_tokens) * 100 if current_tokens > 0 else 0.0
            )
            stats["condensed"] = True
            stats["final_utilization_percent"] = (new_tokens / max_context_length) * 100
            return condensed_messages, stats

        # Store old non-system messages in Mem0 (best-effort).
        try:
            res = self.memory_service.add_memory(
                messages=old_non_system,
                user_id=user_id,
                agent_id=agent_id,
                run_id=session_id,
                project_id=project_id,
                metadata={
                    "type": "context_condensation",
                    "condensed_at": datetime.now().isoformat(),
                    "original_tokens": current_tokens,
                    "model": model_id,
                    "message_count": len(old_non_system),
                },
            )
            if not res.success:
                stats["condensation_error"] = res.message
        except Exception as e:
            stats["condensation_error"] = str(e)
            return messages, stats

        # Avoid stacking multiple summary headers if prior condensations already injected one.
        already_has_summary = any(
            isinstance(m, dict)
            and m.get("role") == "system"
            and isinstance(m.get("content"), str)
            and "Previous Conversation Context" in (m.get("content") or "")
            for m in system_messages
        )

        if not already_has_summary:
            # Calculate adaptive summary limit based on tokens saved
            tokens_saved_estimate = current_tokens - estimate_messages_tokens(
                condensed_messages, model_id, tools=tools, use_cache=True
            )
            adaptive_limit = self._calculate_adaptive_summary_limit(
                tokens_saved=tokens_saved_estimate,
                max_context=max_context_length,
            )
            
            context_summary = self._get_context_summary(
                old_messages=old_non_system,
                user_id=user_id,
                agent_id=agent_id,
                project_id=project_id,
                limit=adaptive_limit,
            )
            if context_summary:
                summary_message = {"role": "system", "content": context_summary}
                condensed_messages = list(system_messages) + [summary_message] + list(recent_non_system)
                stats["summary_added"] = True
                stats["summary_items_retrieved"] = adaptive_limit

        new_tokens = estimate_messages_tokens(condensed_messages, model_id, tools=tools)
        stats["final_tokens"] = new_tokens
        stats["tokens_saved"] = max(0, current_tokens - new_tokens)
        stats["savings_percent"] = (
            (stats["tokens_saved"] / current_tokens) * 100 if current_tokens > 0 else 0.0
        )
        stats["condensed"] = True
        stats["final_utilization_percent"] = (new_tokens / max_context_length) * 100

        logger.info(
            f"Context condensed: {current_tokens} → {new_tokens} tokens "
            f"(saved {stats['tokens_saved']} tokens, {stats['savings_percent']:.1f}% reduction)"
        )
        return condensed_messages, stats

    def _get_context_summary(
        self,
        *,
        old_messages: list[dict[str, Any]],
        user_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Retrieve relevant context from Mem0 based on the tail of old messages.
        
        Args:
            old_messages: Messages being condensed
            user_id: Optional user ID for scoping
            agent_id: Optional agent ID for scoping
            project_id: Optional project ID for scoping
            limit: Number of items to retrieve (default: self.summary_limit)
            
        Returns:
            Formatted context summary string
        """
        if not self.memory_service.is_enabled:
            return ""

        query_messages = old_messages[-3:] if len(old_messages) >= 3 else old_messages
        query_text = " ".join(
            str(m.get("content", ""))
            for m in query_messages
            if isinstance(m, dict) and isinstance(m.get("content"), str)
        ).strip()

        if not query_text:
            return ""

        retrieval_limit = limit if limit is not None else self.summary_limit

        context = self.memory_service.get_relevant_context(
            query=query_text[:500],
            user_id=user_id,
            agent_id=agent_id,
            project_id=project_id,
            limit=max(0, retrieval_limit),
        )
        if not context:
            return ""

        return (
            "## Previous Conversation Context (retrieved from Mem0)\n\n"
            "The following items were retrieved from memory as likely relevant context:\n\n"
            f"{context}\n\n"
            "---\n"
            "Continue the conversation with this context in mind."
        ).strip()