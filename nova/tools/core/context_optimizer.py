"""
Context Optimization Module for Subagent Inputs

This module provides intelligent content truncation and summarization
to prevent context length overflow when processing large tool outputs
(e.g., web search results, file contents).

Features:
- Middle-out transformation: Keeps the most relevant middle section
- Smart chunking: Splits large content into manageable pieces
- LLM-based summarization: Uses the model to create concise summaries
- Token budgeting: Enforces hard limits on input size
"""

import os
import logging
import asyncio
from typing import Optional, List, Tuple, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Token limits (approximate - assumes ~4 chars per token)
DEFAULT_TOKEN_LIMIT = 50000  # Conservative limit for subagent context
HIGH_TOKEN_LIMIT = 40000  # For essential operations
EMERGENCY_TOKEN_LIMIT = 30000  # Absolute minimum

# Character limits based on token approximation
CHAR_LIMIT_DEFAULT = DEFAULT_TOKEN_LIMIT * 4
CHAR_LIMIT_HIGH = HIGH_TOKEN_LIMIT * 4
CHAR_LIMIT_EMERGENCY = EMERGENCY_TOKEN_LIMIT * 4


@dataclass
class OptimizationResult:
    """Result of content optimization."""

    original_length: int
    optimized_length: int
    method_used: str
    content: str
    was_truncated: bool
    chunks: Optional[List[str]] = field(default_factory=lambda: None)


class ContextOptimizer:
    """
    Handles intelligent content optimization for large inputs.

    Strategies:
    1. Direct truncation (when content is moderately large)
    2. Middle-out transformation (preserves context around key sections)
    3. Chunking + Summary (for very large content)
    """

    def __init__(self, model_id: str = None, api_key: str = None):
        self.model_id = model_id or os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5")
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.base_url = "https://openrouter.ai/api/v1"

    async def _create_summary_model(self):
        """Create a lightweight model for summarization."""
        from nova.agent import get_model

        return get_model(self.model_id)

    async def summarize_content(self, content: str, max_tokens: int = 4000) -> str:
        """
        Use LLM to create a concise summary of the content.

        Args:
            content: The content to summarize
            max_tokens: Maximum tokens for the summary

        Returns:
            A concise summary of the content
        """
        if len(content) < 5000:  # Too short to summarize
            return content

        try:
            model = await self._create_summary_model()

            prompt = f"""You are a content summarizer. Create a concise summary of the following content.

IMPORTANT:
- Keep only the MOST IMPORTANT information
- Use bullet points for multiple items
- Preserve technical details, URLs, code snippets, and key facts
- Maximum summary length: {max_tokens} tokens
- Do NOT add introductory phrases like "Here's a summary:"

Content to summarize:
---
{content[:20000]}  # Only send first 20k chars for summarization request
---

Summary:"""

            from agno.agent import Agent

            summary_agent = Agent(
                model=model,
                instructions=[
                    "You are a concise summarizer. Output only the summary, no extra text."
                ],
                markdown=False,
                tools=[],
            )

            response = await summary_agent.arun(prompt)
            return response.content if response.content else content[:1000]

        except Exception as e:
            logger.warning(f"Summarization failed: {e}, using fallback truncation")
            return self._middle_out_transform(content, CHAR_LIMIT_EMERGENCY)

    def _middle_out_transform(self, content: str, max_length: int) -> str:
        """
        Middle-out transformation: Keeps the beginning, end, and a middle section.
        This preserves context while staying within token limits.

        Args:
            content: The content to transform
            max_length: Maximum allowed length

        Returns:
            Transformed content with middle portion preserved
        """
        if len(content) <= max_length:
            return content

        # Calculate section sizes
        # Keep 25% at start, 25% at end, 50% in middle
        section_size = max_length // 4

        start_section = content[:section_size]
        end_section = content[-section_size:]

        # Find the middle section
        middle_start = (len(content) - max_length + section_size * 2) // 2
        middle_end = middle_start + (max_length - section_size * 2)
        middle_section = content[middle_start:middle_end]

        # Add truncation markers
        truncated = (
            f"{start_section}\n\n"
            f"--- [CONTENT TRUNCATED: {len(content) - max_length} chars omitted] ---\n\n"
            f"{middle_section}\n\n"
            f"--- [CONTENT TRUNCATED: {len(content) - max_length} chars omitted] ---\n\n"
            f"{end_section}"
        )

        return truncated

    def _chunk_content(self, content: str, chunk_size: int = 25000) -> List[str]:
        """
        Split content into overlapping chunks for processing.

        Args:
            content: The content to split
            chunk_size: Size of each chunk

        Returns:
            List of content chunks
        """
        chunks = []
        start = 0

        while start < len(content):
            end = start + chunk_size
            chunk = content[start:end]

            # Try to break at a clean boundary (newline)
            if end < len(content):
                last_newline = chunk.rfind("\n")
                if last_newline > chunk_size * 0.8:  # If we can break at > 80% of chunk
                    chunk = chunk[:last_newline]
                    end = start + last_newline

            chunks.append(chunk)
            start = end

        return chunks

    async def optimize(
        self,
        content: str,
        method: str = "auto",
        max_tokens: int = DEFAULT_TOKEN_LIMIT,
        force_summary: bool = False,
    ) -> OptimizationResult:
        """
        Optimize content based on size and method.

        Args:
            content: The content to optimize
            method: 'auto', 'truncate', 'middle-out', 'chunk', 'summarize'
            max_tokens: Maximum tokens allowed
            force_summary: Force LLM summarization

        Returns:
            OptimizationResult with optimized content
        """
        original_length = len(content)
        max_length = max_tokens * 4  # Approximate char limit

        # If content fits, return as-is
        if original_length <= max_length:
            return OptimizationResult(
                original_length=original_length,
                optimized_length=original_length,
                method_used="none",
                content=content,
                was_truncated=False,
            )

        # Choose optimization method
        if method == "auto":
            # Choose based on content size
            if force_summary or original_length > 100000:
                method = "summarize"
            elif original_length > 50000:
                method = "middle-out"
            else:
                method = "truncate"

        optimized_content = content
        method_used = method
        result_chunks = None

        if method == "truncate":
            optimized_content = (
                content[:max_length]
                + f"\n\n[TRUNCATED: {original_length - max_length} chars omitted]"
            )

        elif method == "middle-out":
            optimized_content = self._middle_out_transform(content, max_length)

        elif method == "chunk":
            chunks = self._chunk_content(content)
            result_chunks = chunks  # Store the chunks
            optimized_content = (
                f"[Content split into {len(chunks)} chunks]\n\n"
                + "\n\n---\n\n".join(
                    f"CHUNK {i+1}/{len(chunks)}:\n{c}"
                    for i, c in enumerate(chunks[:10])  # Limit to 10 chunks
                )
            )
            if len(chunks) > 10:
                optimized_content += f"\n\n[... and {len(chunks) - 10} more chunks]"

        elif method == "summarize":
            optimized_content = await self.summarize_content(content, max_tokens)
            method_used = "summarize"

        return OptimizationResult(
            original_length=original_length,
            optimized_length=len(optimized_content),
            method_used=method_used,
            content=optimized_content,
            was_truncated=True,
            chunks=result_chunks,
        )


# Global optimizer instance
_context_optimizer: Optional[ContextOptimizer] = None


def get_context_optimizer() -> ContextOptimizer:
    """Get or create the global context optimizer."""
    global _context_optimizer
    if _context_optimizer is None:
        _context_optimizer = ContextOptimizer()
    return _context_optimizer


async def optimize_subagent_input(
    instructions: str = "",
    task: str = "",
    max_instruction_tokens: int = 10000,
    max_task_tokens: int = HIGH_TOKEN_LIMIT,
) -> Tuple[str, str]:
    """
    Optimize subagent instructions and task to prevent context overflow.

    This function should be called before creating a subagent to ensure
    the inputs don't exceed token limits.

    Args:
        instructions: Subagent instructions/persona
        task: The task to execute
        max_instruction_tokens: Max tokens for instructions
        max_task_tokens: Max tokens for task

    Returns:
        Tuple of (optimized_instructions, optimized_task)
    """
    optimizer = get_context_optimizer()

    # Optimize instructions
    instr_result = await optimizer.optimize(
        instructions,
        method="auto" if len(instructions) > 10000 else "truncate",
        max_tokens=max_instruction_tokens,
        force_summary=False,
    )

    # Optimize task - use more aggressive optimization
    task_result = await optimizer.optimize(
        task,
        method="auto",
        max_tokens=max_task_tokens,
        force_summary=len(task) > 80000,  # Force summary for very large tasks
    )

    # Log optimization
    if instr_result.was_truncated or task_result.was_truncated:
        logger.info(
            f"Context optimization applied: "
            f"instructions {instr_result.method_used} ({instr_result.original_length}->{instr_result.optimized_length}), "
            f"task {task_result.method_used} ({task_result.original_length}->{task_result.optimized_length})"
        )

    return instr_result.content, task_result.content


async def optimize_search_results(search_results: str, max_tokens: int = 10000) -> str:
    """
    Specifically optimized for web search results to prevent context bloat.
    """
    if not search_results or len(str(search_results)) < 15000:
        return str(search_results)

    search_str = str(search_results)
    optimizer = get_context_optimizer()

    # We use a smaller token budget for search results within a subagent loop
    result = await optimizer.optimize(
        search_str, method="middle-out", max_tokens=max_tokens
    )

    if result.was_truncated:
        return f"--- [TRUNCATED SEARCH RESULTS to {max_tokens} tokens] ---\n{result.content}"

    return result.content


# Utility functions for quick optimization
def truncate_middle(content: str, max_length: int = 50000) -> str:
    """Quick middle-out truncation without async."""
    optimizer = get_context_optimizer()
    return optimizer._middle_out_transform(content, max_length)


def smart_chunk(content: str, chunk_size: int = 25000) -> List[str]:
    """Quick chunking without async."""
    optimizer = get_context_optimizer()
    return optimizer._chunk_content(content, chunk_size)


def wrap_tool_output_optimization(tool_func):
    """
    Decorator to automatically optimize tool results, works for both sync and async.
    """
    import functools

    if asyncio.iscoroutinefunction(tool_func):

        @functools.wraps(tool_func)
        async def async_wrapper(*args, **kwargs):
            result = await tool_func(*args, **kwargs)
            if isinstance(result, str) and len(result) > CHAR_LIMIT_HIGH:
                return truncate_middle(result, CHAR_LIMIT_HIGH)
            return result

        return async_wrapper
    else:

        @functools.wraps(tool_func)
        def sync_wrapper(*args, **kwargs):
            result = tool_func(*args, **kwargs)
            if isinstance(result, str) and len(result) > CHAR_LIMIT_HIGH:
                return truncate_middle(result, CHAR_LIMIT_HIGH)
            return result

        return sync_wrapper
