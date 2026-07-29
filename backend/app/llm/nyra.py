"""Real LLM provider, over the OpenAI-compatible wire format.

`base_url` is what makes this portable: NYRA, Azure OpenAI, a vLLM/Ollama
endpoint, or api.openai.com all speak this protocol. Nothing here is
hardcoded to a vendor, and no endpoint appears in source.

Egress note: this network requires everything to traverse an HTTP proxy
(Backend_Scaffold_Notes.md F1). httpx honours HTTPS_PROXY, and the proxy has
been verified to CONNECT to api.openai.com:443, so an external endpoint works
if governance ever permits one. Prefer the internal endpoint (PRD section 15
question 2 is still open on data sensitivity).
"""

from __future__ import annotations

import json
import os

from .provider import Message, Response, ToolCall, ToolSpec


class NyraProvider:
    name = "nyra"

    def __init__(self, base_url: str, model: str, api_key: str,
                 timeout_s: float = 30.0):
        import httpx
        from openai import OpenAI

        # trust_env picks up HTTPS_PROXY, which is mandatory on this network.
        http_client = httpx.Client(timeout=timeout_s, trust_env=True)
        self._client = OpenAI(base_url=base_url,
                              api_key=api_key or "unused",
                              http_client=http_client)
        self.model = model

    @staticmethod
    def _to_wire(m: Message) -> dict:
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id,
                    "content": m.content}
        d: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name,
                              "arguments": json.dumps(tc.arguments)}}
                for tc in m.tool_calls]
        return d

    def chat(self, messages: list[Message],
             tools: list[ToolSpec]) -> Response:
        kwargs: dict = {
            "model": self.model,
            "messages": [self._to_wire(m) for m in messages],
            "temperature": 0,          # determinism matters here (PRD 10)
        }
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]
            kwargs["tool_choice"] = "auto"

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message

        calls: list[ToolCall] = []
        for tc in (choice.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                  arguments=args))

        return Response(content=choice.content or "", tool_calls=calls,
                        model=self.model, provider=self.name)
