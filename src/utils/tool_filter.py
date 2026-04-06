"""Tool filtering utilities for context-based tool selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


class ToolFilter:
    """Filter tools based on conversation context to reduce payload size."""

    _config_cache: dict[str, Any] | None = None
    _config_path = Path("tools_config.json")
    _registry_path = Path("tool_schema_registry.json")

    @classmethod
    def _load_config(cls) -> dict[str, Any]:
        """Load tool configuration.

        Canonical source:
        1) tool_schema_registry.json["policy"] (preferred single-file mode)

        Legacy fallback:
        2) tools_config.json (deprecated)

        3) empty defaults
        """
        if cls._config_cache is not None:
            return cls._config_cache

        def _empty() -> dict[str, Any]:
            return {"tool_categories": {}, "keyword_signals": {}, "essential_tools": []}

        # (1) Registry policy (canonical)
        try:
            if cls._registry_path.exists():
                with cls._registry_path.open("r", encoding="utf-8") as f:
                    reg = json.load(f)
                if isinstance(reg, dict):
                    policy = reg.get("policy")
                    if isinstance(policy, dict):
                        cls._config_cache = {
                            "tool_categories": policy.get("tool_categories", {}) or {},
                            "keyword_signals": policy.get("keyword_signals", {}) or {},
                            "essential_tools": policy.get("essential_tools", []) or [],
                        }
                        logger.debug(
                            f"Loaded tool filter policy from {cls._registry_path}['policy']"
                        )
                        return cls._config_cache
        except Exception as e:
            logger.error(f"Failed to load tool filter policy from registry: {e}")

        # (2) Legacy tools_config.json (deprecated)
        try:
            if cls._config_path.exists():
                with cls._config_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    cls._config_cache = data
                    logger.warning(
                        f"Loaded deprecated tool filter config from {cls._config_path}; "
                        f"prefer {cls._registry_path}['policy']"
                    )
                    return cls._config_cache
        except Exception as e:
            logger.error(f"Failed to load tool config from {cls._config_path}: {e}")

        # (3) Empty defaults
        logger.warning(
            f"No tool filter policy found at {cls._registry_path}['policy']; "
            f"using empty defaults"
        )
        cls._config_cache = _empty()
        return cls._config_cache

    @classmethod
    def reload_config(cls) -> None:
        """Reload configuration from file (clears cache)."""
        cls._config_cache = None
        cls._load_config()
        logger.info("Tool filter configuration reloaded")

    @classmethod
    def get_tool_categories(cls) -> dict[str, list[str]]:
        """Get tool categories from config."""
        return cls._load_config().get("tool_categories", {})

    @classmethod
    def get_keyword_signals(cls) -> dict[str, list[str]]:
        """Get keyword signals from config."""
        return cls._load_config().get("keyword_signals", {})

    @classmethod
    def get_essential_tools(cls) -> list[str]:
        """Get essential tools from config."""
        return cls._load_config().get("essential_tools", [])

    @staticmethod
    def filter_tools(
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tools: int = 50,
        enable_filtering: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Filter tools based on conversation context.

        Args:
            tools: List of available tools
            messages: Conversation messages for context
            max_tools: Maximum number of tools to include (default: 50)
            enable_filtering: Whether to enable filtering (default: True)

        Returns:
            Tuple of (filtered_tools, filter_stats)
        """
        if not enable_filtering or not tools or len(tools) <= max_tools:
            return tools, {
                "filtered": False,
                "original_count": len(tools) if tools else 0,
                "filtered_count": len(tools) if tools else 0,
                "reason": "filtering_disabled" if not enable_filtering else "under_limit",
            }

        logger.info(f"Filtering {len(tools)} tools down to max {max_tools}")

        # Extract recent context from messages
        recent_context = ToolFilter._extract_recent_context(messages, limit=3)

        # Detect active categories based on context
        active_categories = ToolFilter._detect_active_categories(recent_context)

        # Build tool priority scores
        tool_scores = ToolFilter._score_tools(tools, active_categories, recent_context)

        # Select top tools
        filtered_tools = ToolFilter._select_top_tools(
            tools, tool_scores, max_tools, active_categories
        )

        stats = {
            "filtered": True,
            "original_count": len(tools),
            "filtered_count": len(filtered_tools),
            "active_categories": list(active_categories),
            "reduction_percent": (1 - len(filtered_tools) / len(tools)) * 100,
        }

        logger.info(
            f"Filtered tools: {len(tools)} → {len(filtered_tools)} "
            f"({stats['reduction_percent']:.1f}% reduction). "
            f"Active categories: {', '.join(active_categories)}"
        )

        return filtered_tools, stats

    @staticmethod
    def filter_tools_for_injection(
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tools: int = 50,
        enable_filtering: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Filter tools specifically for *server-side tool schema injection*.

        Differences vs [`ToolFilter.filter_tools()`](src/utils/tool_filter.py:1):
        - Does NOT force-include `essential_tools`.
        - Does NOT default to `file_operations` when no intent is detected.

        Rationale:
        - If the client omitted `tools`, injecting tools unconditionally can cause models to
          "tool chase" (e.g. calling read_file with workspace/app name).
        - Injection should only happen when the conversation provides positive evidence
          of tool intent (keywords or explicit tool mentions).
        """
        if not enable_filtering or not tools or max_tools <= 0:
            return [], {
                "filtered": False,
                "original_count": len(tools) if tools else 0,
                "filtered_count": 0,
                "reason": "filtering_disabled" if not enable_filtering else "no_tools",
            }

        recent_context = ToolFilter._extract_recent_context(messages, limit=3)
        active_categories = ToolFilter._detect_active_categories_for_injection(recent_context)

        if not active_categories:
            return [], {
                "filtered": True,
                "original_count": len(tools),
                "filtered_count": 0,
                "active_categories": [],
                "reason": "no_active_categories",
            }

        tool_scores = ToolFilter._score_tools_for_injection(
            tools, active_categories, recent_context
        )
        filtered_tools = ToolFilter._select_top_tools_for_injection(tools, tool_scores, max_tools)

        stats = {
            "filtered": True,
            "original_count": len(tools),
            "filtered_count": len(filtered_tools),
            "active_categories": list(active_categories),
            "reduction_percent": (1 - len(filtered_tools) / len(tools)) * 100 if tools else 0.0,
        }

        return filtered_tools, stats

    @staticmethod
    def _detect_active_categories_for_injection(context: str) -> set[str]:
        """Detect relevant tool categories for injection.

        Unlike `_detect_active_categories()`, this does NOT add a default category.

        If `keyword_signals` is empty/missing (common in minimal configs), fall back to
        conservative heuristics:
        - If the user text mentions a tool name from `tool_categories`, activate that category.
        - If the user text contains file/path-like hints, activate `file_operations`.

        This keeps injection "opt-in" (requires positive evidence in the message), while
        avoiding a misconfiguration where *no* injection ever happens.
        """
        active: set[str] = set()

        keyword_signals = ToolFilter.get_keyword_signals()
        for category, keywords in keyword_signals.items():
            if any(keyword in context for keyword in keywords):
                active.add(category)

        # Fallback: infer categories by explicit tool-name mentions.
        tool_categories = ToolFilter.get_tool_categories()
        for category, tool_names in tool_categories.items():
            for tool_name in tool_names:
                if isinstance(tool_name, str) and tool_name and tool_name.lower() in context:
                    active.add(category)
                    break

        # Fallback: detect file operations intent from file-like tokens (extensions/paths)
        # plus action verbs. This is intentionally limited to avoid "tool chasing" on unrelated chats.
        if "file_operations" not in active:
            fileish = any(
                token in context
                for token in (
                    ".md",
                    ".py",
                    ".json",
                    ".txt",
                    ".yaml",
                    ".yml",
                    "/",
                    "\\",
                    "readme",
                )
            )
            actionish = any(
                token in context
                for token in (
                    "read ",
                    "open ",
                    "show ",
                    "list ",
                    "search ",
                    "find ",
                    "summarize ",
                    "contents",
                    "content",
                    "file",
                    "folder",
                    "directory",
                )
            )
            if fileish and actionish:
                active.add("file_operations")

        return active

    @staticmethod
    def _score_tools_for_injection(
        tools: list[dict[str, Any]],
        active_categories: set[str],
        context: str,
    ) -> dict[str, float]:
        """Score tools for injection without force-including essentials."""
        scores: dict[str, float] = {}
        tool_categories = ToolFilter.get_tool_categories()
        keyword_signals = ToolFilter.get_keyword_signals()

        for tool in tools:
            tool_name = tool.get("function", {}).get("name", "")
            if not tool_name:
                continue

            score = 0.0

            # Category match
            for category in active_categories:
                category_tools = tool_categories.get(category, [])
                if any(tool_name.startswith(ct) or ct in tool_name for ct in category_tools):
                    score += 10.0

            # Tool explicitly mentioned
            if tool_name.lower() in context:
                score += 20.0

            # Description keyword matches
            description = tool.get("function", {}).get("description", "").lower()
            for category in active_categories:
                keywords = keyword_signals.get(category, [])
                if any(kw in description for kw in keywords):
                    score += 5.0

            scores[tool_name] = score

        return scores

    @staticmethod
    def _select_top_tools_for_injection(
        tools: list[dict[str, Any]],
        scores: dict[str, float],
        max_tools: int,
    ) -> list[dict[str, Any]]:
        """Select top-scoring tools for injection."""
        sorted_tools = sorted(
            tools,
            key=lambda t: scores.get(t.get("function", {}).get("name", ""), 0.0),
            reverse=True,
        )

        selected: list[dict[str, Any]] = []
        for tool in sorted_tools:
            if len(selected) >= max_tools:
                break

            tool_name = tool.get("function", {}).get("name", "")
            if not tool_name:
                continue

            # Only inject tools that scored positively for the detected context.
            # This avoids injecting irrelevant control tools (e.g. attempt_completion)
            # when the client omitted `tools`.
            if scores.get(tool_name, 0.0) <= 0.0:
                continue

            selected.append(tool)

        return selected

    @staticmethod
    def _extract_recent_context(messages: list[dict[str, Any]], limit: int = 3) -> str:
        """Extract text from recent messages for analysis.

        Args:
            messages: List of conversation messages
            limit: Number of recent messages to consider

        Returns:
            Combined text from recent messages
        """
        recent_messages = messages[-limit:] if messages else []
        context_parts = []

        for msg in recent_messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                context_parts.append(content)
            elif isinstance(content, list):
                # Handle multi-part content
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        context_parts.append(part.get("text", ""))

        return " ".join(context_parts).lower()

    @staticmethod
    def _detect_active_categories(context: str) -> set[str]:
        """Detect relevant tool categories from context.

        Args:
            context: Text context to analyze

        Returns:
            Set of active category names
        """
        active = set()

        # Check keyword signals
        keyword_signals = ToolFilter.get_keyword_signals()
        for category, keywords in keyword_signals.items():
            if any(keyword in context for keyword in keywords):
                active.add(category)

        # If no categories detected, include file_operations as default
        if not active:
            active.add("file_operations")

        return active

    @staticmethod
    def _score_tools(
        tools: list[dict[str, Any]],
        active_categories: set[str],
        context: str,
    ) -> dict[str, float]:
        """Score tools based on relevance to active categories and context.

        Args:
            tools: List of available tools
            active_categories: Detected active categories
            context: Text context

        Returns:
            Dictionary mapping tool names to relevance scores
        """
        scores = {}
        essential_tools = ToolFilter.get_essential_tools()
        tool_categories = ToolFilter.get_tool_categories()
        keyword_signals = ToolFilter.get_keyword_signals()

        for tool in tools:
            tool_name = tool.get("function", {}).get("name", "")
            if not tool_name:
                continue

            score = 0.0

            # Essential tools get maximum score
            if tool_name in essential_tools:
                score = 100.0
            else:
                # Score based on category match
                for category in active_categories:
                    category_tools = tool_categories.get(category, [])
                    if any(tool_name.startswith(ct) or ct in tool_name for ct in category_tools):
                        score += 10.0

                # Score based on name appearing in context
                if tool_name.lower() in context:
                    score += 20.0

                # Score based on description keywords
                description = tool.get("function", {}).get("description", "").lower()
                for category in active_categories:
                    keywords = keyword_signals.get(category, [])
                    if any(kw in description for kw in keywords):
                        score += 5.0

            scores[tool_name] = score

        return scores

    @staticmethod
    def _select_top_tools(
        tools: list[dict[str, Any]],
        scores: dict[str, float],
        max_tools: int,
        active_categories: set[str],
    ) -> list[dict[str, Any]]:
        """Select top-scoring tools up to max_tools limit.

        Args:
            tools: List of available tools
            scores: Tool relevance scores
            max_tools: Maximum number of tools to select
            active_categories: Active tool categories

        Returns:
            List of selected tools
        """
        essential_tools = ToolFilter.get_essential_tools()

        # Sort tools by score (descending)
        sorted_tools = sorted(
            tools,
            key=lambda t: scores.get(t.get("function", {}).get("name", ""), 0.0),
            reverse=True,
        )

        # Ensure essential tools are included
        selected = []
        essential_count = 0

        for tool in sorted_tools:
            tool_name = tool.get("function", {}).get("name", "")

            # Always include essential tools
            if tool_name in essential_tools:
                selected.append(tool)
                essential_count += 1
                continue

            # Add high-scoring tools until max_tools reached
            if len(selected) < max_tools:
                selected.append(tool)

        logger.debug(
            f"Selected {len(selected)} tools: "
            f"{essential_count} essential, "
            f"{len(selected) - essential_count} context-relevant"
        )

        return selected[:max_tools]