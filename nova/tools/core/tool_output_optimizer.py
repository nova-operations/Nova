"""
Web Search Result Optimizer

This module provides specialized optimization for web search results,
which are often the source of context overflow due to their verbose nature.
"""

import os
import logging
from typing import Optional
from nova.tools.core.context_optimizer import (
    get_context_optimizer,
    OptimizationResult,
    CHAR_LIMIT_HIGH,
)

logger = logging.getLogger(__name__)


def optimize_web_search_result(
    raw_result: str, max_results: int = 10, include_snippets: bool = True
) -> str:
    """
    Optimize web search results for consumption.

    This function:
    1. Parses search results and extracts key information
    2. Limits the number of results
    3. Truncates long snippets
    4. Formats for readability

    Args:
        raw_result: Raw search result text
        max_results: Maximum number of results to include
        include_snippets: Whether to include search snippets

    Returns:
        Optimized search results
    """
    if len(raw_result) < 10000:
        return raw_result

    optimizer = get_context_optimizer()

    # Try to parse and selectively keep results
    lines = raw_result.split("\n")
    kept_lines = []
    result_count = 0
    url_count = 0

    for line in lines:
        # Count result headers (common patterns)
        if any(
            pattern in line.lower() for pattern in ["result", "===", "---", "title:"]
        ):
            result_count += 1
            if result_count > max_results:
                kept_lines.append(
                    f"\n[... {result_count - max_results} more results omitted ...]"
                )
                break

        # Keep URLs but truncate if too long
        if "http" in line.lower():
            url_count += 1
            if len(line) > 200:
                line = line[:200] + "..."

        # Skip extremely long content lines (likely full page dumps)
        if len(line) > 1500:
            line = line[:1500] + " [...truncated]"

        kept_lines.append(line)

    optimized = "\n".join(kept_lines)

    # If still too large, apply middle-out
    if len(optimized) > CHAR_LIMIT_HIGH:
        optimized = optimizer._middle_out_transform(optimized, CHAR_LIMIT_HIGH)
        logger.info(
            f"Web search results further truncated: {len(raw_result)} -> {len(optimized)}"
        )

    return optimized


async def optimize_tool_output(
    tool_name: str, output: str, max_tokens: int = 20000
) -> str:
    """
    Generic tool output optimizer that applies different strategies
    based on the tool type.

    Args:
        tool_name: Name of the tool that produced the output
        output: Raw tool output
        max_tokens: Maximum tokens for the output

    Returns:
        Optimized output
    """
    # Large token limit for tool outputs - we want to preserve data
    max_chars = max_tokens * 4

    if len(output) <= max_chars:
        return output

    optimizer = get_context_optimizer()

    # Tool-specific optimization
    if "search" in tool_name.lower() or "web" in tool_name.lower():
        # For search tools, use specialized optimization
        return optimize_web_search_result(output)

    # For other tools, use middle-out transformation
    result = await optimizer.optimize(
        output, method="middle-out", max_tokens=max_tokens
    )

    logger.info(
        f"Tool output optimized for {tool_name}: "
        f"{result.original_length} -> {result.optimized_length} chars "
        f"using {result.method_used}"
    )

    return result.content


# Quick utility for sync contexts
def quick_truncate(content: str, max_chars: int = 50000) -> str:
    """
    Quick middle-out truncation for sync contexts.

    Args:
        content: Content to truncate
        max_chars: Maximum characters to keep

    Returns:
        Truncated content
    """
    if len(content) <= max_chars:
        return content

    optimizer = get_context_optimizer()
    return optimizer._middle_out_transform(content, max_chars)
