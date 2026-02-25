"""
Tests for Context Optimization Module

These tests verify that the middle-out transformation and chunking
strategies work correctly to prevent context overflow.
"""

import pytest
import asyncio
from nova.tools.context_optimizer import (
    ContextOptimizer,
    get_context_optimizer,
    OptimizationResult,
    CHAR_LIMIT_DEFAULT,
    CHAR_LIMIT_HIGH,
    CHAR_LIMIT_EMERGENCY,
)


class TestMiddleOutTransform:
    """Tests for middle-out transformation strategy."""

    def setup_method(self):
        self.optimizer = get_context_optimizer()

    def test_no_truncation_needed(self):
        """Content smaller than max_length should be returned as-is."""
        content = "Short content"
        result = self.optimizer._middle_out_transform(content, 100)
        assert result == content

    def test_middle_out_truncation(self):
        """Large content should be truncated with start, middle, end preserved."""
        # Create content with identifiable sections
        content = "START" + "A" * 100 + "MIDDLE" + "B" * 100 + "END"

        result = self.optimizer._middle_out_transform(content, 50)

        # Should contain truncation markers
        assert "TRUNCATED" in result
        # Should preserve start
        assert result.startswith("START")
        # Should preserve end
        assert result.endswith("END")

    def test_exact_max_length(self):
        """Content exactly at max_length should not be truncated."""
        content = "x" * 100
        result = self.optimizer._middle_out_transform(content, 100)
        assert result == content

    def test_preserves_structure(self):
        """Truncated content should maintain readable structure."""
        # Multi-line content
        content = "\n".join([f"Line {i}: " + "x" * 50 for i in range(20)])

        result = self.optimizer._middle_out_transform(content, 500)

        assert "TRUNCATED" in result
        assert "Line 0:" in result  # Start preserved
        # End lines might be preserved depending on truncation


class TestChunking:
    """Tests for content chunking strategy."""

    def setup_method(self):
        self.optimizer = get_context_optimizer()

    def test_small_content_single_chunk(self):
        """Small content should return single chunk."""
        content = "Short content"
        chunks = self.optimizer._chunk_content(content, chunk_size=100)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_large_content_multiple_chunks(self):
        """Large content should be split into multiple chunks."""
        content = "x" * 1000
        chunks = self.optimizer._chunk_content(content, chunk_size=100)
        assert len(chunks) == 10

    def test_respects_chunk_size(self):
        """Chunks should not exceed chunk_size (approximately)."""
        content = "x" * 1000
        chunks = self.optimizer._chunk_content(content, chunk_size=250)

        for chunk in chunks[:-1]:  # All but last chunk
            assert len(chunk) <= 260  # Allow small overrun for boundary finding


class TestOptimizeFunction:
    """Tests for the main optimize function."""

    def setup_method(self):
        self.optimizer = get_context_optimizer()

    def test_auto_truncate_small(self):
        """Small content with auto method should not be truncated."""
        content = "Short content"

        # Run in async context
        result = asyncio.run(self.optimizer.optimize(content, method="auto"))

        assert not result.was_truncated
        assert result.content == content

    def test_method_selection(self):
        """Different methods should be selected based on content size."""
        # Small content
        small = "x" * 100
        result = asyncio.run(
            self.optimizer.optimize(small, method="auto", max_tokens=100)
        )
        assert result.method_used == "none"

        # Medium content - should use truncate or middle-out
        medium = "x" * 30000
        result = asyncio.run(
            self.optimizer.optimize(medium, method="auto", max_tokens=2000)
        )
        assert result.method_used in ["truncate", "middle-out"]

        # Very large content - would use summarize but we can't test LLM
        large = "x" * 200000
        result = asyncio.run(
            self.optimizer.optimize(large, method="truncate", max_tokens=1000)
        )
        assert result.was_truncated

    def test_explicit_methods(self):
        """Explicit method selection should work correctly."""
        content = "x" * 50000

        # Test truncate
        result = asyncio.run(
            self.optimizer.optimize(content, method="truncate", max_tokens=1000)
        )
        assert result.method_used == "truncate"

        # Test middle-out
        result = asyncio.run(
            self.optimizer.optimize(content, method="middle-out", max_tokens=1000)
        )
        assert result.method_used == "middle-out"

        # Test chunk
        result = asyncio.run(
            self.optimizer.optimize(content, method="chunk", max_tokens=1000)
        )
        assert result.method_used == "chunk"
        assert result.chunks is not None


class TestQuickUtilities:
    """Tests for quick utility functions."""

    def test_quick_truncate(self):
        """quick_truncate should work without async."""
        from nova.tools.tool_output_optimizer import quick_truncate

        # Small content
        result = quick_truncate("short", 100)
        assert result == "short"

        # Large content
        result = quick_truncate("x" * 10000, 100)
        assert len(result) < 10000
        assert "TRUNCATED" in result

    def test_optimize_web_search_result(self):
        """Web search result optimizer should handle large results."""
        from nova.tools.tool_output_optimizer import optimize_web_search_result

        # Create mock search results
        results = []
        for i in range(20):
            results.append(f"Result {i+1}: Title for result {i+1}")
            results.append(f"URL: https://example.com/page{i+1}")
            results.append(f"Snippet: " + "x" * 500)  # Long snippet
        raw_results = "\n".join(results)

        # Should be optimized
        optimized = optimize_web_search_result(raw_results, max_results=5)

        # Should limit results
        assert "Result 1:" in optimized
        # Should indicate truncation if there were more results
        assert len(optimized) < len(raw_results)


class TestGlobalOptimizer:
    """Tests for the global optimizer instance."""

    def test_singleton_pattern(self):
        """get_context_optimizer should return the same instance."""
        opt1 = get_context_optimizer()
        opt2 = get_context_optimizer()
        assert opt1 is opt2

    def test_token_limits_defined(self):
        """Token limits should be properly defined."""
        assert CHAR_LIMIT_DEFAULT > 0
        assert CHAR_LIMIT_HIGH > 0
        assert CHAR_LIMIT_EMERGENCY > 0
        assert CHAR_LIMIT_EMERGENCY < CHAR_LIMIT_HIGH < CHAR_LIMIT_DEFAULT


# Run with: pytest nova/tools/test_context_optimizer.py -v
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
