"""
Middle-Out Prompt Transformer for LLM Context Management

This module provides automatic prompt compression when conversation history
exceeds the 204800 token limit. It implements a "middle-out" transformation
that keeps the system prompt and latest user request intact while compressing
the middle (conversation history).

Key Features:
- Automatic detection of oversized prompts
- Middle-out transformation preserving start/end of conversation
- System prompt preservation
- Latest user message preservation
- Configurable token limits
"""

import os
import re
import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Token limits
DEFAULT_TOKEN_LIMIT = 204800  # 200k tokens (leave some buffer)
SAFE_TOKEN_LIMIT = 180000    # Safe limit with buffer

# Character-to-token ratio (conservative estimate)
CHARS_PER_TOKEN = 3.5

# Token limits in characters
MAX_PROMPT_CHARS = int(DEFAULT_TOKEN_LIMIT * CHARS_PER_TOKEN)
SAFE_PROMPT_CHARS = int(SAFE_TOKEN_LIMIT * CHARS_PER_TOKEN)


@dataclass
class TransformResult:
    """Result of prompt transformation."""
    original_length: int
    transformed_length: int
    was_transformed: bool
    method: str
    preserved_sections: List[str] = field(default_factory=list)
    transformed_prompt: str = ""


class MiddleOutTransformer:
    """
    Transforms large prompts using middle-out strategy.
    """
    
    def __init__(self, max_tokens: int = DEFAULT_TOKEN_LIMIT, safe_pct: float = 0.9):
        self.max_tokens = max_tokens
        self.max_chars = int(max_tokens * CHARS_PER_TOKEN)
        self.safe_chars = int(self.max_chars * safe_pct)
        
    def estimate_tokens(self, text: str) -> int:
        return int(len(text) / CHARS_PER_TOKEN)
    
    def extract_system_prompt(self, prompt: str) -> Tuple[str, str]:
        """Extract system prompt from the full prompt."""
        human_patterns = [
            r'\n\nHuman:', r'\nHuman:', r'\n\nUser:', r'\nUser:',
            r'\n\nuser:', r'\nuser:',
        ]
        
        for pattern in human_patterns:
            match = re.search(pattern, prompt, re.IGNORECASE)
            if match:
                system_part = prompt[:match.start()]
                remaining = prompt[match.start():]
                if len(system_part) > 100 and len(remaining) > 100:
                    return system_part, remaining
        
        split_point = int(len(prompt) * 0.2)
        if split_point > 2000:
            return prompt[:split_point], prompt[split_point:]
        
        return "", prompt
    
    def extract_latest_message(self, prompt: str) -> Tuple[str, str]:
        """Extract the most recent user message from prompt."""
        human_patterns = [
            r'\n\nHuman:.*$', r'\nHuman:.*$', r'\n\nUser:.*$', r'\nUser:.*$',
        ]
        
        latest_pos = -1
        for pattern in human_patterns:
            match = re.search(pattern, prompt, re.MULTILINE | re.IGNORECASE)
            if match and match.end() > latest_pos:
                latest_pos = match.end()
        
        if latest_pos > 0:
            return prompt[latest_pos - 100:], prompt[:latest_pos]
        
        split_point = int(len(prompt) * 0.8)
        return prompt[split_point:], prompt[:split_point]
    
    def apply_middle_out(self, content: str, max_length: int) -> str:
        """Apply middle-out transformation to content."""
        content_len = len(content)
        
        if content_len <= max_length:
            return content
        
        # Preserve 25% at start, 25% at end, compress middle
        preserve_start = max_length // 4
        preserve_end = max_length // 4
        middle_size = max_length - preserve_start - preserve_end
        
        start_section = content[:preserve_start]
        end_section = content[-preserve_end:] if content_len > preserve_end else content
        
        # Get middle section from the middle of remaining content
        middle_start = preserve_start
        if middle_size > 0:
            middle_section = content[middle_start:middle_start + middle_size]
        else:
            middle_section = ""
        
        omitted = content_len - len(start_section) - len(middle_section) - len(end_section)
        
        marker = f"--- [TRUNCATED: {omitted} chars omitted] ---\n"
        
        result = start_section + "\n" + marker + middle_section + "\n" + marker + end_section
        
        # If still too long, just truncate the middle more aggressively
        if len(result) > max_length:
            # Simple approach: just keep start and end
            result = start_section + "\n" + marker + end_section
            if len(result) > max_length:
                result = result[:max_length]
        
        return result
    
    def transform(self, prompt: str) -> TransformResult:
        """Transform a prompt using middle-out strategy."""
        original_length = len(prompt)
        
        # Check if transformation is needed
        if original_length <= self.max_chars:
            return TransformResult(
                original_length=original_length,
                transformed_length=original_length,
                was_transformed=False,
                method="none",
                preserved_sections=["full_prompt"],
                transformed_prompt=prompt
            )
        
        logger.warning(
            f"Prompt exceeds max limit: {original_length} chars "
            f"(~{original_length // 4} tokens, max: {self.max_chars}). "
            f"Applying middle-out transformation."
        )
        
        # Step 1: Extract system prompt
        system_prompt, after_system = self.extract_system_prompt(prompt)
        
        # Step 2: Extract latest message
        latest_message, history = self.extract_latest_message(after_system)
        
        # Step 3: Calculate space for history
        header_size = 200
        available_for_history = self.max_chars - len(system_prompt) - len(latest_message) - header_size
        
        if available_for_history < 3000:
            available_for_history = 3000
        
        # Step 4: Apply middle-out to history
        if len(history) > available_for_history:
            history = self.apply_middle_out(history, int(available_for_history))
        
        # Step 5: Rebuild prompt with header
        header = f"[CONTEXT COMPRESSED: {original_length // 4} -> ~{(len(system_prompt) + len(history) + len(latest_message)) // 4} tokens]\n"
        transformed_prompt = header + system_prompt + "\n\n" + history + "\n\n" + latest_message
        
        # Step 6: Final truncation if still too large (with buffer)
        if len(transformed_prompt) > self.max_chars:
            # Truncate but preserve that it's been compressed
            # Reserve space for header
            transformed_prompt = transformed_prompt[:self.max_chars - 50]
            if "TRUNCATED" not in transformed_prompt:
                transformed_prompt = transformed_prompt + "\n\n[... TRUNCATED ...]"
        
        return TransformResult(
            original_length=original_length,
            transformed_length=len(transformed_prompt),
            was_transformed=True,
            method="middle-out",
            preserved_sections=["system_prompt", "latest_message", "history"],
            transformed_prompt=transformed_prompt
        )


# Global transformer instance
_transformer: Optional[MiddleOutTransformer] = None


def get_transformer(max_tokens: int = DEFAULT_TOKEN_LIMIT) -> MiddleOutTransformer:
    """Get or create the global transformer instance."""
    global _transformer
    if _transformer is None:
        _transformer = MiddleOutTransformer(max_tokens)
    return _transformer


def transform_prompt(prompt: str, max_tokens: int = DEFAULT_TOKEN_LIMIT) -> Tuple[str, bool]:
    """Convenience function to transform a prompt."""
    transformer = get_transformer(max_tokens)
    result = transformer.transform(prompt)
    return result.transformed_prompt, result.was_transformed


__all__ = [
    'MiddleOutTransformer',
    'TransformResult', 
    'get_transformer',
    'transform_prompt',
    'DEFAULT_TOKEN_LIMIT',
    'SAFE_TOKEN_LIMIT',
    'SAFE_PROMPT_CHARS',
    'MAX_PROMPT_CHARS',
]