"""Provider protocol and factory.

PRD section 4.3: NYRA is the interface layer, not the calculation engine and not
the system of record. This seam enforces that structurally -- a provider can
only return text and tool-call requests. It has no database handle, so it
cannot read or write anything the agent loop does not hand it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Message:
    role: str                                   # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]                  # JSON Schema

    def to_openai(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name,
                             "description": self.description,
                             "parameters": self.parameters}}


@dataclass
class Response:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    provider: str = ""


class Provider(Protocol):
    name: str
    model: str

    def chat(self, messages: list[Message],
             tools: list[ToolSpec]) -> Response: ...


def get_provider() -> Provider:
    """Config-driven. Defaults to the offline stub so tests never need network.

    LLM_BASE_URL   internal NYRA / Azure OpenAI gateway (unset -> EchoProvider)
    LLM_MODEL      model id
    LLM_API_KEY    bearer token
    LLM_TIMEOUT_S  request timeout, default 30
    """
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    if not base_url:
        from .echo import EchoProvider
        return EchoProvider()

    from .nyra import NyraProvider
    return NyraProvider(
        base_url=base_url,
        model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        api_key=os.environ.get("LLM_API_KEY", ""),
        timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "30")),
    )
