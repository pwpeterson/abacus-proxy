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
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService
from src.utils.token_estimator import estimate_messages_tokens


class ContextManager:
    """Manages context size using Mem0 for intelligent condensation.
    
    Enhanced with:
    - Model-aware dynamic thresholds (vLLM vs large-context models)
    - Message importance scoring for better retention decisions
    - System message compression to prevent bloat
    - Adaptive summary retrieval based on available budget
    - Code block compression for large code snippets
    - Differential compression for similar consecutive messages
    - Aggressive system message section deduplication
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
        enable_code_compression: bool = True,
        enable_differential_compression: bool = True,
        enable_aggressive_system_dedup: bool = True,
        code_compression_min_lines: int = 20,
        differential_similarity_threshold: float = 0.85,
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
        self.enable_code_compression = bool(enable_code_compression)
        self.enable_differential_compression = bool(enable_differential_compression)
        self.enable_aggressive_system_dedup = bool(enable_aggressive_system_dedup)
        self.code_compression_min_lines = int(code_compression_min_lines)
        self.differential_similarity_threshold = float(differential_similarity_threshold)

    def _calculate_dynamic_threshold(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_context_length: int,
        base_threshold: float | None = None,
    ) -> float:
        """Calculate model-aware dynamic threshold based on context size and request characteristics.
        
        Model-aware thresholds:
        - Large context models (32K+): Use most of available context (0.70-0.80)
          Examples: gpt-5.2 (128K), Claude (200K), gpt-4 (128K)
        - Medium context models (8K-32K): Moderate usage (0.50-0.60)
        - Small context models (<8K): Tight control for vLLM (0.30-0.35)
          Examples: Qwen2.5-Coder (4K), local models
        
        Additional adjustments for request type:
        - Architecture/planning: +10% (can use more context)
        - Coding with tools: Baseline
        - Large system prompts: -5% (trigger earlier for safety)
        
        Args:
            messages: Chat messages to analyze
            tools: Optional tool schemas
            max_context_length: Maximum context length for the model
            base_threshold: Override base threshold (default: self.context_threshold)
            
        Returns:
            Adjusted threshold (0.0-1.0)
        """
        if not self.enable_dynamic_threshold:
            return base_threshold or self.context_threshold
        
        # Model-aware base threshold
        if max_context_length >= 32000:
            # Large context models (gpt-5.2, Claude, etc.)
            # Use most of available context before condensing
            model_base_threshold = 0.75
        elif max_context_length >= 8000:
            # Medium context models
            model_base_threshold = 0.55
        else:
            # Small context models (vLLM, local models)
            # Need tight control to prevent overflow
            model_base_threshold = 0.35
        
        # Start with model-aware base
        threshold = model_base_threshold
        
        # Calculate total input size
        total_chars = sum(
            len(str(m.get("content", "")))
            for m in messages
            if isinstance(m, dict)
        )
        
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
        
        # Architecture/planning keywords
        arch_keywords = [
            "design", "architecture", "plan", "strategy", "approach",
            "system", "component", "workflow",
        ]
        arch_score = sum(1 for kw in arch_keywords if kw in content_lower)
        
        # Adjust threshold based on request characteristics
        if arch_score >= 2:
            # Architecture tasks can use more context
            threshold = min(threshold + 0.10, 1.0)
        elif has_tools and coding_score >= 3:
            # Coding tasks with tools - use model base threshold
            pass
        
        # Large system prompt adjustment (safety margin)
        if total_chars > 30000:
            threshold = max(threshold - 0.05, 0.30)
        
        # Respect configured base threshold as upper limit
        configured_threshold = base_threshold or self.context_threshold
        threshold = min(threshold, configured_threshold)
        
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

    def _extract_system_sections(self, content: str) -> dict[str, str]:
        """Extract major sections from system message content.
        
        Identifies common sections like:
        - Roo rules blocks
        - MCP server definitions
        - Tool descriptions
        - Project context
        
        Args:
            content: System message content
            
        Returns:
            Dict mapping section name to content
        """
        sections = {}
        
        # Pattern 1: Roo rules blocks (# Rules from ...)
        rules_pattern = r'(# Rules from.*?(?=# Rules from|$))'
        rules_matches = re.findall(rules_pattern, content, re.DOTALL)
        for i, match in enumerate(rules_matches):
            sections[f"roo_rules_{i}"] = match.strip()
        
        # Pattern 2: MCP server blocks (## <server-name> (...))
        mcp_pattern = r'(## \w[\w-]+ \([^)]+\).*?(?=## \w[\w-]+ \(|$))'
        mcp_matches = re.findall(mcp_pattern, content, re.DOTALL)
        for i, match in enumerate(mcp_matches):
            sections[f"mcp_server_{i}"] = match.strip()
        
        # Pattern 3: Tool descriptions sections
        tools_pattern = r'(### .* Tools.*?(?=###|$))'
        tools_matches = re.findall(tools_pattern, content, re.DOTALL)
        for i, match in enumerate(tools_matches):
            sections[f"tools_section_{i}"] = match.strip()
        
        # Pattern 4: Large standalone sections (>=500 chars between headers)
        standalone_pattern = r'(?:^|\n)((?:(?!^#+ ).)+)'
        standalone_matches = re.findall(standalone_pattern, content, re.MULTILINE | re.DOTALL)
        for i, match in enumerate(standalone_matches):
            if len(match.strip()) >= 500:
                sections[f"standalone_{i}"] = match.strip()
        
        return sections

    def _compress_system_messages(
        self,
        system_msgs: list[dict[str, Any]],
        max_tokens: int,
        model_id: str,
    ) -> list[dict[str, Any]]:
        """Compress system messages when they exceed budget.
        
        Strategy:
        1. Deduplicate identical content (if aggressive dedup disabled)
        2. OR: Hash sections separately and deduplicate (if aggressive dedup enabled)
        3. Merge similar sections
        4. Truncate if still over budget
        
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
        
        # Use aggressive section-level deduplication if enabled
        if self.enable_aggressive_system_dedup:
            compressed = self._compress_system_messages_aggressive(system_msgs, model_id)
        else:
            # Standard message-level deduplication
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
        
        # Check if deduplication was enough
        current_tokens = estimate_messages_tokens(compressed, model_id)
        
        if current_tokens <= max_tokens:
            dedup_type = "section-level" if self.enable_aggressive_system_dedup else "message-level"
            logger.info(
                f"System message {dedup_type} deduplication successful: "
                f"{len(system_msgs)} → {len(compressed)} messages"
            )
            return compressed
        
        # Truncate proportionally if still over budget
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

    def _compress_system_messages_aggressive(
        self,
        system_msgs: list[dict[str, Any]],
        model_id: str,
    ) -> list[dict[str, Any]]:
        """Aggressively deduplicate system messages at section level.
        
        Instead of hashing entire messages, hash individual sections
        (Roo rules blocks, MCP server definitions, tool descriptions)
        and reconstruct messages with unique sections only.
        
        Args:
            system_msgs: System messages to compress
            model_id: Model ID for token estimation
            
        Returns:
            Compressed system messages with section-level deduplication
        """
        compressed = []
        seen_section_hashes = set()
        sections_saved = 0
        
        for msg in system_msgs:
            content = msg.get("content", "")
            if not isinstance(content, str):
                compressed.append(msg)
                continue
            
            # Extract sections from this message
            sections = self._extract_system_sections(content)
            
            if not sections:
                # No extractable sections, use whole-message hash
                content_hash = hashlib.md5(content.encode()).hexdigest()
                if content_hash not in seen_section_hashes:
                    seen_section_hashes.add(content_hash)
                    compressed.append(msg)
                else:
                    sections_saved += 1
                continue
            
            # Deduplicate at section level
            unique_sections = []
            for section_name, section_content in sections.items():
                section_hash = hashlib.md5(section_content.encode()).hexdigest()
                
                if section_hash not in seen_section_hashes:
                    seen_section_hashes.add(section_hash)
                    unique_sections.append(section_content)
                else:
                    sections_saved += 1
                    logger.debug(f"Deduplicating section: {section_name} (hash: {section_hash[:8]})")
            
            # Reconstruct message with unique sections only
            if unique_sections:
                new_content = "\n\n".join(unique_sections)
                compressed.append({
                    "role": "system",
                    "content": new_content,
                })
        
        if sections_saved > 0:
            logger.info(f"Aggressive system dedup: removed {sections_saved} duplicate sections")
        
        return compressed

    def _compress_code_blocks(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Compress large code blocks in messages before storing to Mem0.
        
        Strategy:
        - Extract code blocks (```...```)
        - If code block has >= min_lines, replace with summary
        - Preserve small code blocks intact
        
        Args:
            messages: Messages to compress
            
        Returns:
            Tuple of (compressed_messages, tokens_saved_estimate)
        """
        if not self.enable_code_compression:
            return messages, 0
        
        compressed = []
        chars_saved = 0
        blocks_compressed = 0
        
        code_block_pattern = r'```(\w*)\n(.*?)```'
        
        for msg in messages:
            if not isinstance(msg, dict):
                compressed.append(msg)
                continue
            
            content = msg.get("content", "")
            if not isinstance(content, str) or "```" not in content:
                compressed.append(msg)
                continue
            
            # Find all code blocks
            def replace_code_block(match):
                nonlocal chars_saved, blocks_compressed
                
                language = match.group(1) or "code"
                code = match.group(2)
                
                lines = code.strip().split('\n')
                
                # Only compress if exceeds threshold
                if len(lines) < self.code_compression_min_lines:
                    return match.group(0)  # Keep original
                
                # Generate summary
                first_lines = lines[:3]
                last_lines = lines[-2:] if len(lines) > 5 else []
                
                summary_parts = [f"```{language}"]
                summary_parts.extend(first_lines)
                summary_parts.append(f"... [{len(lines) - len(first_lines) - len(last_lines)} lines omitted] ...")
                summary_parts.extend(last_lines)
                summary_parts.append("```")
                summary_parts.append(f"\n[Code block compressed: {len(lines)} lines → summary]")
                
                summary = "\n".join(summary_parts)
                
                chars_saved += len(match.group(0)) - len(summary)
                blocks_compressed += 1
                
                return summary
            
            new_content = re.sub(code_block_pattern, replace_code_block, content, flags=re.DOTALL)
            
            compressed.append({
                **msg,
                "content": new_content,
            })
        
        # Rough estimate: 1 char ≈ 0.25 tokens
        tokens_saved = int(chars_saved * 0.25)
        
        if blocks_compressed > 0:
            logger.info(
                f"Code compression: {blocks_compressed} blocks compressed, "
                f"~{tokens_saved} tokens saved"
            )
        
        return compressed, tokens_saved

    def _calculate_message_similarity(self, msg1: str, msg2: str) -> float:
        """Calculate similarity between two message contents.
        
        Args:
            msg1: First message content
            msg2: Second message content
            
        Returns:
            Similarity ratio (0.0-1.0)
        """
        # Use SequenceMatcher for fuzzy string matching
        matcher = SequenceMatcher(None, msg1, msg2)
        return matcher.ratio()

    def _apply_differential_compression(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Apply differential compression to similar consecutive messages.
        
        Strategy:
        - Compare consecutive messages
        - If similarity >= threshold, store diff instead of full content
        - Preserve first occurrence of each similar group
        
        Args:
            messages: Messages to compress
            
        Returns:
            Tuple of (compressed_messages, tokens_saved_estimate)
        """
        if not self.enable_differential_compression:
            return messages, 0
        
        if len(messages) < 2:
            return messages, 0
        
        compressed = []
        chars_saved = 0
        diffs_applied = 0
        
        prev_msg = None
        prev_content = None
        
        for msg in messages:
            if not isinstance(msg, dict):
                compressed.append(msg)
                prev_msg = msg
                prev_content = None
                continue
            
            content = msg.get("content", "")
            if not isinstance(content, str):
                compressed.append(msg)
                prev_msg = msg
                prev_content = None
                continue
            
            # Skip tool calls/results (preserve intact)
            if msg.get("tool_calls") or msg.get("tool_call_id"):
                compressed.append(msg)
                prev_msg = msg
                prev_content = content
                continue
            
            # Compare with previous message
            if prev_content and len(content) > 100 and len(prev_content) > 100:
                similarity = self._calculate_message_similarity(prev_content, content)
                
                if similarity >= self.differential_similarity_threshold:
                    # Store diff instead of full content
                    matcher = SequenceMatcher(None, prev_content, content)
                    opcodes = matcher.get_opcodes()
                    
                    diff_parts = []
                    for tag, i1, i2, j1, j2 in opcodes:
                        if tag == 'replace':
                            diff_parts.append(f"[-{prev_content[i1:i2]}-](+{content[j1:j2]}+)")
                        elif tag == 'delete':
                            diff_parts.append(f"[-{prev_content[i1:i2]}-]")
                        elif tag == 'insert':
                            diff_parts.append(f"(+{content[j1:j2]}+)")
                    
                    diff_summary = "".join(diff_parts)
                    
                    # Only use diff if it's actually smaller
                    if len(diff_summary) < len(content) * 0.7:
                        compressed.append({
                            **msg,
                            "content": f"[DIFF from previous message (similarity: {similarity:.1%})]:\n{diff_summary}",
                        })
                        chars_saved += len(content) - len(diff_summary)
                        diffs_applied += 1
                        prev_msg = msg
                        prev_content = content
                        continue
            
            # No compression, store as-is
            compressed.append(msg)
            prev_msg = msg
            prev_content = content
        
        # Rough estimate: 1 char ≈ 0.25 tokens
        tokens_saved = int(chars_saved * 0.25)
        
        if diffs_applied > 0:
            logger.info(
                f"Differential compression: {diffs_applied} diffs applied, "
                f"~{tokens_saved} tokens saved"
            )
        
        return compressed, tokens_saved

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

    @staticmethod
    def _extract_tool_call_ids(msg: dict[str, Any]) -> set[str]:
        """Extract OpenAI-style tool call IDs from an assistant message."""
        ids: set[str] = set()
        tc = msg.get("tool_calls")
        if not isinstance(tc, list):
            return ids
        for item in tc:
            if not isinstance(item, dict):
                continue
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                ids.add(call_id)
        return ids

    @classmethod
    def _ensure_tool_call_output_pairs(
        cls,
        messages: list[dict[str, Any]],
        keep_positions: set[int],
    ) -> set[int]:
        """Ensure we never keep a tool call without its tool output (and vice versa).

        OpenAI-compatible chat history must include a `role="tool"` message with
        `tool_call_id=<id>` after any assistant message that includes a tool call
        with id `<id>`. If we drop that tool output but keep the tool call, upstream
        providers reject the request with e.g.:
        "No tool output found for function call <id>".
        """
        call_positions_by_id: dict[str, set[int]] = {}
        output_positions_by_id: dict[str, set[int]] = {}

        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                continue

            # Assistant tool calls
            for call_id in cls._extract_tool_call_ids(m):
                call_positions_by_id.setdefault(call_id, set()).add(i)

            # Tool outputs
            if m.get("role") == "tool":
                tool_call_id = m.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id:
                    output_positions_by_id.setdefault(tool_call_id, set()).add(i)

        all_ids = set(call_positions_by_id) | set(output_positions_by_id)
        if not all_ids:
            return keep_positions

        # Expand until stable (handles multi-step expansion, though usually 1 pass).
        changed = True
        while changed:
            changed = False
            for tid in all_ids:
                call_pos = call_positions_by_id.get(tid, set())
                out_pos = output_positions_by_id.get(tid, set())

                if (keep_positions & call_pos) and out_pos and not (keep_positions & out_pos):
                    keep_positions |= out_pos
                    changed = True

                if (keep_positions & out_pos) and call_pos and not (keep_positions & call_pos):
                    keep_positions |= call_pos
                    changed = True

        return keep_positions

    @classmethod
    def _audit_dangling_tool_calls(cls, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Return info about dangling tool calls / outputs in a message list."""
        call_ids: set[str] = set()
        output_ids: set[str] = set()

        for m in messages:
            if not isinstance(m, dict):
                continue
            call_ids |= cls._extract_tool_call_ids(m)
            if m.get("role") == "tool":
                tool_call_id = m.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id:
                    output_ids.add(tool_call_id)

        missing_outputs = sorted(call_ids - output_ids)
        missing_calls = sorted(output_ids - call_ids)
        return {
            "tool_calls": len(call_ids),
            "tool_outputs": len(output_ids),
            "missing_outputs": missing_outputs,
            "missing_calls": missing_calls,
        }

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
        - Model-aware dynamic thresholds (large vs small context models)
        - Message importance scoring for better retention decisions
        - System message compression to prevent bloat
        - Adaptive summary retrieval based on available budget
        - Code block compression for large code snippets (2-5K tokens saved)
        - Differential compression for similar messages (1-3K tokens saved)
        - Aggressive system section deduplication (2-5K tokens saved)
        - Preserve ALL existing system messages (don't store them in Mem0)
        - Condense only non-system history into Mem0 and keep a tail slice
        - Include tool-schema tokens in the threshold calculation when provided
        - Insert the retrieved summary AFTER the existing system messages
        """
        current_tokens = estimate_messages_tokens(messages, model_id, tools=tools, use_cache=True)
        
        # Use model-aware dynamic threshold
        dynamic_threshold = self._calculate_dynamic_threshold(
            messages=messages,
            tools=tools,
            max_context_length=max_context_length,
        )
        threshold_tokens = int(max_context_length * dynamic_threshold)

        # DEBUG: Log threshold check
        logger.debug(
            f"[CONDENSATION CHECK] current_tokens={current_tokens} threshold_tokens={threshold_tokens} "
            f"max_context={max_context_length} dynamic_threshold={dynamic_threshold:.2f} "
            f"enable_condensation={enable_condensation} tools_count={len(tools) if tools else 0}"
        )

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
            "code_compression_enabled": self.enable_code_compression,
            "differential_compression_enabled": self.enable_differential_compression,
            "aggressive_system_dedup_enabled": self.enable_aggressive_system_dedup,
            "tool_pairing_expansions": 0,
            "dangling_tool_calls_detected": False,
            "code_blocks_compressed": 0,
            "differential_diffs_applied": 0,
            "system_sections_deduped": 0,
        }

        if not enable_condensation or current_tokens <= threshold_tokens:
            logger.debug(
                f"[CONDENSATION SKIPPED] enable={enable_condensation} "
                f"current_tokens={current_tokens} <= threshold={threshold_tokens}"
            )
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
            
            # Keep top-scored messages (by object identity).
            index_by_obj_id = {id(m): idx for idx, m in enumerate(non_system_messages)}
            keep_positions = {
                index_by_obj_id[id(msg)]
                for msg, _score in scored_messages[:keep_non_system_count]
                if id(msg) in index_by_obj_id
            }

            before = len(keep_positions)
            keep_positions = self._ensure_tool_call_output_pairs(non_system_messages, keep_positions)
            after = len(keep_positions)
            stats["tool_pairing_expansions"] = max(0, after - before)

            recent_non_system = [
                m for idx, m in enumerate(non_system_messages) if idx in keep_positions
            ]
            old_non_system = [
                m for idx, m in enumerate(non_system_messages) if idx not in keep_positions
            ]
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

        # Apply compression strategies before storing to Mem0
        compressed_old_messages = old_non_system
        compression_tokens_saved = 0
        
        # 1. Code block compression
        if self.enable_code_compression:
            compressed_old_messages, code_tokens = self._compress_code_blocks(compressed_old_messages)
            compression_tokens_saved += code_tokens
            if code_tokens > 0:
                stats["code_blocks_compressed"] = code_tokens
        
        # 2. Differential compression
        if self.enable_differential_compression:
            compressed_old_messages, diff_tokens = self._apply_differential_compression(compressed_old_messages)
            compression_tokens_saved += diff_tokens
            if diff_tokens > 0:
                stats["differential_diffs_applied"] = diff_tokens

        # Store compressed old messages in Mem0 (best-effort).
        try:
            res = self.memory_service.add_memory(
                messages=compressed_old_messages,
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
                    "compression_tokens_saved": compression_tokens_saved,
                    "code_compression": self.enable_code_compression,
                    "differential_compression": self.enable_differential_compression,
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
        stats["compression_tokens_saved"] = compression_tokens_saved

        logger.info(
            f"Context condensed: {current_tokens} → {new_tokens} tokens "
            f"(saved {stats['tokens_saved']} tokens, {stats['savings_percent']:.1f}% reduction)"
        )

        if compression_tokens_saved > 0:
            logger.info(f"Additional compression savings: ~{compression_tokens_saved} tokens")

        audit = self._audit_dangling_tool_calls(condensed_messages)
        if audit.get("missing_outputs") or audit.get("missing_calls"):
            stats["dangling_tool_calls_detected"] = True
            logger.warning(
                "Condensed messages contain dangling tool history "
                f"missing_outputs={audit.get('missing_outputs')} missing_calls={audit.get('missing_calls')}"
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
