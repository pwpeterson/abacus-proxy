"""System message trimming for vLLM's 18K token limit.

vLLM context budget:
- Total: 18,000 tokens
- Response: 4,000 tokens (max_tokens cap)
- Input: 14,000 tokens available
- System message target: ~20,000 chars (~5-6K tokens, ~40% of input budget)
- User message gets: ~8-9K tokens (~60% of input budget for conversation history)

Strategy: Aggressive trimming to leave room for conversation history.
"""

import re
from loguru import logger


def trim_system_for_vllm(content):
    """Trim system message to ~20K chars for vLLM (leaves 8-9K tokens for user message).
    
    Handles both string content and multimodal list format:
    - String: "text content"
    - List: [{"type": "text", "text": "content"}, ...]
    
    Keeps:
    - Core identity and capabilities
    - Markdown linking rules (essential)
    - Tool use guidelines (essential)
    - MCP server tools (filtered)
    - Core operational rules only
    
    Strips:
    - Verbose examples
    - Mode descriptions (keep list only)
    - Rules file content (keep headers only)
    - System information (essential parts only)
    
    Args:
        content: System message content (string or list)
        
    Returns:
        Trimmed system message (~20K chars, ~5-6K tokens)
        Returns same type as input (string or list)
    """
    # Handle multimodal content (list format)
    if isinstance(content, list):
        logger.info(f"[VLLM TRIM] Detected multimodal list format with {len(content)} items")
        
        # Extract text from list items
        text_parts = []
        for idx, item in enumerate(content):
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                text_parts.append(text)
                logger.debug(f"[VLLM TRIM] Item {idx}: extracted {len(text)} chars")
        
        if not text_parts:
            # No text found in list, return unchanged
            logger.warning("[VLLM TRIM] No text items found in list, returning unchanged")
            return content
        
        # Concatenate all text parts
        text_content = "\n\n".join(text_parts)
        logger.info(f"[VLLM TRIM] Concatenated {len(text_parts)} text items → {len(text_content)} chars")
        
        # Apply trimming to concatenated text
        trimmed_text = _trim_text_content(text_content)
        
        # Return as list format with single text item
        result = [{"type": "text", "text": trimmed_text}]
        logger.info(f"[VLLM TRIM] Returning multimodal list with {len(trimmed_text)} chars")
        return result
    
    # Handle string content
    if isinstance(content, str):
        logger.info(f"[VLLM TRIM] Detected string format with {len(content)} chars")
        return _trim_text_content(content)
    
    # Unknown type, return unchanged
    logger.warning(f"[VLLM TRIM] Unknown content type: {type(content)}, returning unchanged")
    return content


def _trim_text_content(content: str) -> str:
    """Internal helper to trim text content aggressively.
    
    Target: ~20K chars (~5-6K tokens) to leave room for user message.
    
    Args:
        content: Text content to trim
        
    Returns:
        Trimmed text (~20K chars, ~5-6K tokens)
    """
    # More aggressive trimming for vLLM to leave budget for user message
    # Target: 20K chars system + 30K chars user = 50K total (~14K tokens)
    
    # Extract sections to keep
    sections_to_keep = []
    
    # 1. Everything before "MODES" section - keep core identity and tool use
    before_modes = re.search(r'^.*?(?=\n\n====\n\nMODES|\Z)', content, re.DOTALL)
    if before_modes:
        core_text = before_modes.group(0)
        # Trim to first 10K chars (includes identity, markdown rules, tool use, MCP servers)
        if len(core_text) > 10000:
            core_text = core_text[:10000] + "\n\n...(trimmed for vLLM budget)"
        sections_to_keep.append(core_text)
    
    # 2. MODES section - keep mode names only, skip descriptions
    modes_section = re.search(r'====\n\nMODES.*?(?=\n\n====|\Z)', content, re.DOTALL)
    if modes_section:
        modes_text = modes_section.group(0)
        # Extract just mode names and slugs
        mode_entries = re.findall(r'\* "([^"]+)" mode \(([^)]+)\)', modes_text)
        if mode_entries:
            trimmed_modes = "====\n\nMODES\n\n- Available modes:\n"
            for name, slug in mode_entries[:10]:  # Limit to first 10 modes
                trimmed_modes += f'  * "{name}" ({slug})\n'
            sections_to_keep.append(trimmed_modes.strip())
    
    # 3. RULES section - keep headers only, remove content
    rules_section = re.search(r'====\n\nRULES', content)
    if rules_section:
        # Just add marker that rules exist (actual rules are in mode-specific files)
        sections_to_keep.append("====\n\nRULES\n\n(Rules condensed for vLLM - see mode-specific behavior)")
    
    # 4. SYSTEM INFORMATION section - keep minimal essentials only
    system_info = re.search(r'====\n\nSYSTEM INFORMATION.*?(?=\n\n====|\Z)', content, re.DOTALL)
    if system_info:
        sys_text = system_info.group(0)
        # Keep only first 1500 chars (OS, shell, directories)
        if len(sys_text) > 1500:
            sys_text = sys_text[:1500] + "\n\n...(system info truncated)"
        sections_to_keep.append(sys_text.strip())
    
    # 5. USER'S CUSTOM INSTRUCTIONS - keep first 1000 chars only
    custom_instructions = re.search(r'====\n\nUSER\'S CUSTOM INSTRUCTIONS.*?(?=\n\n<|\Z)', content, re.DOTALL)
    if custom_instructions:
        custom_text = custom_instructions.group(0)
        if len(custom_text) > 1000:
            custom_text = custom_text[:1000] + "\n...(custom instructions truncated)"
        sections_to_keep.append(custom_text.strip())
    
    trimmed = "\n\n".join(sections_to_keep)
    
    # Hard limit: ensure we never exceed 20K chars for system message
    if len(trimmed) > 20000:
        logger.warning(f"[VLLM TRIM] Still over budget after sectional trimming: {len(trimmed)} chars")
        # More aggressive: keep only first 20K chars
        trimmed = trimmed[:20000] + "\n\n...(content hard-trimmed to 20K char limit for vLLM)"
    
    orig_len = len(content)
    new_len = len(trimmed)
    reduction_pct = ((orig_len - new_len) / orig_len * 100) if orig_len > 0 else 0
    
    logger.info(
        f"[VLLM SYSTEM TRIM] {orig_len} → {new_len} chars ({reduction_pct:.1f}% reduction, target ≤20K for user message budget)"
    )
    
    return trimmed
