"""LLM provider seam.

Nothing outside this package imports `openai`. Swapping NYRA for Azure OpenAI,
a local model, or a test stub is a config change, not a code change.
"""

from .provider import Message, Provider, ToolCall, ToolSpec, get_provider

__all__ = ["Message", "Provider", "ToolCall", "ToolSpec", "get_provider"]
