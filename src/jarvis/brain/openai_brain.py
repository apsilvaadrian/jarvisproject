"""Cérebro GPT (OpenAI Chat Completions com function calling).

Também funciona com qualquer servidor compatível com a API da OpenAI
(Ollama, LM Studio, Groq, OpenRouter...) trocando OPENAI_BASE_URL e OPENAI_MODEL.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import settings
from ..tools import ToolExecutor
from .base import MAX_TOOL_ROUNDS, Brain, BrainError, system_prompt, tool_result_text


# provedor -> (endereço base, atributo da chave, atributo do modelo)
PROVEDORES = {
    "openai": (None, "openai_key", "openai_model"),
    "groq": ("https://api.groq.com/openai/v1", "groq_key", "groq_model"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_key", "openrouter_model"),
    "ollama": (None, None, "ollama_model"),
}


def base_url(provedor: str) -> str:
    if provedor == "openai":
        return settings.openai_base_url.rstrip("/")
    if provedor == "ollama":
        return settings.ollama_url.rstrip("/") + "/v1"
    return PROVEDORES[provedor][0]  # type: ignore[return-value]


class OpenAIBrain(Brain):
    name = "openai"

    def __init__(self, provedor: str = "openai") -> None:
        self.name = self.provedor = provedor
        _, attr_key, _ = PROVEDORES[provedor]
        key = getattr(settings, attr_key) if attr_key else ""
        local = provedor == "ollama" or (provedor == "openai" and "api.openai.com" not in settings.openai_base_url)
        if not key and not local:
            raise BrainError(f"Chave de API do {provedor} não configurada.")
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        if provedor == "openrouter":
            headers.update({"HTTP-Referer": "https://github.com/", "X-Title": "JARVIS"})
        self.url = base_url(provedor) + "/chat/completions"
        self.http = httpx.Client(timeout=120 if provedor == "ollama" else 90, headers=headers)
        self.tools = [{"type": "function", "function": t} for t in self.tool_specs()]

    @property
    def model(self) -> str:
        if self.provedor == "ollama":
            from ..config import ollama_models
            modelo = settings.ollama_model or (ollama_models() or [""])[0]
            if not modelo:
                raise BrainError("Nenhum modelo instalado no Ollama (rode: ollama pull qwen3).")
            return modelo
        return getattr(settings, PROVEDORES[self.provedor][2])

    def respond(self, history: list[dict[str, str]], executor: ToolExecutor) -> str:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt()}]
        messages += [{"role": m["role"], "content": m["text"]} for m in self.trimmed(history)]
        for _ in range(MAX_TOOL_ROUNDS):
            r = self.http.post(self.url, json={"model": self.model, "messages": messages, "tools": self.tools})
            if r.status_code != 200:
                raise BrainError(f"{self.provedor} respondeu {r.status_code}: {r.text[:300]}")
            msg = r.json()["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            if not calls:
                return (msg.get("content") or "").strip()
            for call in calls:
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = executor.run(call["function"]["name"], args)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_result_text(out)})
        return "Executei as ações solicitadas, " + settings.tratamento + "."
