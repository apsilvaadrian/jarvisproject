"""Cérebro Claude (Anthropic Messages API com tool use)."""
from __future__ import annotations

from typing import Any

import httpx

from ..config import settings
from ..tools import ToolExecutor
from .base import MAX_TOOL_ROUNDS, Brain, BrainError, system_prompt, tool_result_text

API_URL = "https://api.anthropic.com/v1/messages"


class ClaudeBrain(Brain):
    name = "claude"

    def __init__(self) -> None:
        if not settings.anthropic_key:
            raise BrainError("ANTHROPIC_API_KEY não configurada.")
        self.http = httpx.Client(timeout=60, headers={
            "x-api-key": settings.anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                      for t in self.tool_specs()]

    def respond(self, history: list[dict[str, str]], executor: ToolExecutor) -> str:
        messages: list[dict[str, Any]] = [{"role": m["role"], "content": m["text"]} for m in self.trimmed(history)]
        for _ in range(MAX_TOOL_ROUNDS):
            r = self.http.post(API_URL, json={
                "model": settings.anthropic_model,
                "max_tokens": 1024,
                # cache_control: o prompt de sistema + ferramentas são reaproveitados entre turnos
                "system": [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
                "tools": self.tools,
                "messages": messages,
            })
            if r.status_code != 200:
                raise BrainError(f"Claude respondeu {r.status_code}: {r.text[:300]}")
            data = r.json()
            content = data.get("content", [])
            messages.append({"role": "assistant", "content": content})
            calls = [b for b in content if b.get("type") == "tool_use"]
            if data.get("stop_reason") != "tool_use" or not calls:
                return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            results = []
            for call in calls:
                out = executor.run(call["name"], call.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": tool_result_text(out)})
            messages.append({"role": "user", "content": results})
        return "Executei as ações solicitadas, " + settings.tratamento + "."
