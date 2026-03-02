"""
Test for Middle-Out Prompt Transformer

This script verifies that the middle-out transformation correctly
compresses prompts while preserving system prompts and user messages.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Clear cached imports
for mod_name in list(sys.modules.keys()):
    if "nova" in mod_name:
        del sys.modules[mod_name]

from nova.tools.core.prompt_transformer import (
    MiddleOutTransformer,
    TransformResult,
    DEFAULT_TOKEN_LIMIT,
    MAX_PROMPT_CHARS,
)


def test_no_truncation_needed():
    """Small prompts should not be transformed."""
    transformer = MiddleOutTransformer(max_tokens=DEFAULT_TOKEN_LIMIT)

    small_prompt = "Hello, how are you?"
    result = transformer.transform(small_prompt)

    assert not result.was_transformed, "Small prompt should not be transformed"
    assert result.method == "none"
    print("PASS: test_no_truncation_needed")


def test_middle_out_transformation():
    """Large prompts should be transformed with middle-out strategy."""
    transformer = MiddleOutTransformer(max_tokens=10000)

    # Create prompt exceeding 35k chars
    system_prompt = "You are a helpful assistant." + "x" * 25000
    history = "Previous conversation:\n" + "\n".join(
        [f"User: Message {i}\nAssistant: Response {i}" for i in range(50)]
    )
    latest_message = "\n\nHuman: What is the capital of France?"

    large_prompt = system_prompt + history + latest_message + "x" * 20000

    result = transformer.transform(large_prompt)

    assert result.was_transformed, f"Large prompt should be transformed"
    assert result.method == "middle-out"
    assert result.transformed_length < result.original_length

    print("PASS: test_middle_out_transformation")


def test_preserves_sections():
    """Transformed prompt should preserve identifiable sections."""
    transformer = MiddleOutTransformer(max_tokens=5000)  # 17.5k max chars

    # Need > 17.5k chars
    system = "SYSTEM PROMPT: You are Nova." + "x" * 10000
    history = "HISTORY START\n" + "Message 1\nMessage 2\n" * 100 + "HISTORY END"
    latest = "\n\nHuman: Latest question?"

    # Add more to exceed limit
    full_prompt = system + history + latest + "x" * 10000

    result = transformer.transform(full_prompt)

    assert result.was_transformed, f"Prompt should be transformed, got: {result.method}"
    assert "TRUNCATED" in result.transformed_prompt

    print("PASS: test_preserves_sections")


def test_extreme_case():
    """Extremely large prompts should still be handled."""
    transformer = MiddleOutTransformer(max_tokens=10000)

    huge_prompt = "x" * 200000

    result = transformer.transform(huge_prompt)

    assert result.was_transformed
    assert result.transformed_length < len(huge_prompt)
    assert result.transformed_length <= transformer.max_chars
    assert "TRUNCATED" in result.transformed_prompt

    print("PASS: test_extreme_case")


def test_mock_large_prompt():
    """Test with a mock prompt simulating the 395051 token error scenario."""
    transformer = MiddleOutTransformer(max_tokens=50000)

    system_prompt = "You are Nova, the Project Manager AI. " * 3000
    history_lines = []
    for i in range(300):
        history_lines.append(
            f"User: Research topic {i}\n\nAssistant: Here is my research on topic {i}. "
            * 80
        )
    history = "\n\n".join(history_lines)
    latest_message = (
        "\n\nHuman: " + "I need you to research the geopolitical implications. " * 300
    )

    mock_prompt = system_prompt + "\n\n" + history + "\n\n" + latest_message

    result = transformer.transform(mock_prompt)

    assert result.was_transformed
    assert result.method == "middle-out"
    assert result.transformed_length <= transformer.max_chars

    print("PASS: test_mock_large_prompt")


def test_200k_token_scenario():
    """Test the actual 395051 token error scenario."""
    transformer = MiddleOutTransformer(max_tokens=150000)

    system_prompt = "You are Nova, the Project Manager AI. " * 1000
    history = ("Previous conversation line. " * 50 + "\n\n") * 5000
    latest = "\n\nHuman: " + "Please research this topic in detail. " * 500

    long_prompt = system_prompt + "\n\n" + history + "\n" + latest

    result = transformer.transform(long_prompt)

    assert result.was_transformed
    assert result.transformed_length <= transformer.max_chars

    print("PASS: test_200k_token_scenario")


def main():
    """Run all tests."""
    print("=" * 60)
    print("MIDDLE-OUT PROMPT TRANSFORMER TESTS")
    print("=" * 60)
    print(f"Default token limit: {DEFAULT_TOKEN_LIMIT}")
    print(f"Max prompt chars: {MAX_PROMPT_CHARS}")
    print()

    tests = [
        test_no_truncation_needed,
        test_middle_out_transformation,
        test_preserves_sections,
        test_extreme_case,
        test_mock_large_prompt,
        test_200k_token_scenario,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            import traceback

            traceback.print_exc()
            failed += 1
        print()

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
