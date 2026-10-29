"""Fábrica de cérebros: claude | openai | gemini | groq | openrouter | ollama | contas | local."""
from __future__ import annotations

import re

from .base import Brain, BrainError

LABELS = {"claude": "Claude (API)", "openai": "GPT (API)", "gemini": "Gemini (API)", "groq": "Groq (API grátis)",
          "openrouter": "OpenRouter (API)", "ollama": "Ollama (local)",
          "claude-conta": "Claude (sua conta)", "openai-conta": "ChatGPT (sua conta)",
          "gemini-conta": "Gemini (sua conta Google)", "local": "Offline"}


def create_brain(name: str) -> Brain:
    if name in ("claude-conta", "openai-conta", "gemini-conta"):
        from .cli import ClaudeContaBrain, CodexContaBrain, GeminiContaBrain
        return {"claude-conta": ClaudeContaBrain, "openai-conta": CodexContaBrain,
                "gemini-conta": GeminiContaBrain}[name]()
    if name == "claude":
        from .claude import ClaudeBrain
        return ClaudeBrain()
    if name in ("openai", "groq", "openrouter", "ollama"):
        from .openai_brain import OpenAIBrain
        return OpenAIBrain(name)
    if name == "gemini":
        from .gemini import GeminiBrain
        return GeminiBrain()
    if name == "local":
        from .local import LocalBrain
        return LocalBrain()
    raise BrainError(f"Cérebro desconhecido: {name}")


KEY_LINKS = {
    "claude": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/apikey",
    "groq": "https://console.groq.com/keys",
    "openrouter": "https://openrouter.ai/keys",
}
# Provedores que seguem a API da OpenAI: /models com "Authorization: Bearer"
_COMPATIVEIS = {"groq": "https://api.groq.com/openai/v1", "openrouter": "https://openrouter.ai/api/v1"}


def validate_key(brain: str, key: str) -> tuple[bool | None, str]:
    """Testa a chave listando os modelos do provedor (não gasta tokens).

    Retorna (True, msg) se válida, (False, msg) se recusada e (None, msg) se não foi
    possível testar (ex.: sem internet) — nesse caso a chave é salva mesmo assim.
    """
    import httpx

    from ..config import settings

    if brain == "claude":
        req = ("https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif brain == "openai":
        req = (settings.openai_base_url.rstrip("/") + "/models", {"authorization": f"Bearer {key}"})
    elif brain == "gemini":
        req = ("https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": key})
    elif brain in _COMPATIVEIS:
        # A lista de modelos do OpenRouter é pública; /key confere a chave de verdade
        url = _COMPATIVEIS[brain] + ("/key" if brain == "openrouter" else "/models")
        req = (url, {"authorization": f"Bearer {key}"})
    else:
        return False, "Cérebro desconhecido."
    try:
        r = httpx.get(req[0], headers=req[1], timeout=10)
    except httpx.HTTPError as exc:
        return None, f"Não consegui testar a chave agora ({exc.__class__.__name__}); ela foi salva mesmo assim."
    if r.status_code in (401, 403) or (brain == "gemini" and r.status_code == 400):
        return False, f"{LABELS[brain]} recusou a chave (HTTP {r.status_code}). Confira se copiou a chave inteira."
    if r.status_code >= 400:
        return None, f"{LABELS[brain]} respondeu HTTP {r.status_code}; a chave foi salva mesmo assim."
    return True, "Chave validada."


# Modelos que não servem para conversa com ferramentas (áudio, imagem, embeddings...)
_SKIP = ("embedding", "tts", "transcribe", "audio", "realtime", "image", "dall-e", "whisper", "moderation",
         "search", "instruct", "live", "robotics", "computer-use", "aqa", "babbage", "davinci", "codex",
         "banana")


def _version_key(model_id: str) -> tuple:
    """Ordena por versão (mais nova primeiro); estáveis antes de 'preview'; apelidos ('latest') no fim."""
    m = re.search(r"-(\d+(?:\.\d+)?)", model_id)
    version = float(m.group(1)) if m else -1.0
    return (m is not None, version, "preview" not in model_id and "exp" not in model_id, model_id)


def filter_models(brain: str, payload: dict) -> list[dict[str, str]]:
    """Converte a resposta de 'listar modelos' de cada provedor em [{id, label}], só modelos de chat."""
    out: list[dict[str, str]] = []
    if brain == "claude":
        for m in payload.get("data", []):
            out.append({"id": m["id"], "label": m.get("display_name") or m["id"]})
    elif brain == "openai":
        items = sorted(payload.get("data", []), key=lambda m: m.get("created", 0), reverse=True)
        for m in items:
            mid = m["id"]
            if mid.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")) and not any(s in mid for s in _SKIP):
                out.append({"id": mid, "label": mid})
    elif brain == "gemini":
        for m in payload.get("models", []):
            mid = m.get("name", "").removeprefix("models/")
            if (mid.startswith("gemini") and "generateContent" in m.get("supportedGenerationMethods", [])
                    and not any(s in mid for s in _SKIP)):
                out.append({"id": mid, "label": m.get("displayName") or mid})
        out.sort(key=lambda m: _version_key(m["id"]), reverse=True)
    elif brain in _COMPATIVEIS:
        for m in payload.get("data", []):
            mid = m["id"]
            params = m.get("supported_parameters")
            if any(s in mid for s in ("whisper", "tts", "guard", "embed", "orpheus")) or (params and "tools" not in params):
                continue                                  # o JARVIS precisa de modelos com ferramentas
            out.append({"id": mid, "label": m.get("name") or mid})
        if brain == "openrouter":
            out.insert(0, {"id": "openrouter/auto", "label": "Automático (OpenRouter escolhe)"})
    elif brain == "ollama":
        out = [{"id": m["name"], "label": m["name"]} for m in payload.get("models", [])]
    return out


def list_models(brain: str, key: str) -> list[dict[str, str]]:
    """Pergunta ao provedor quais modelos essa chave pode usar."""
    import httpx

    from ..config import settings

    if brain == "claude":
        r = httpx.get("https://api.anthropic.com/v1/models", params={"limit": 100},
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=10)
    elif brain == "openai":
        r = httpx.get(settings.openai_base_url.rstrip("/") + "/models",
                      headers={"authorization": f"Bearer {key}"} if key else {}, timeout=10)
    elif brain == "gemini":
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 1000},
                      headers={"x-goog-api-key": key}, timeout=10)
    elif brain in _COMPATIVEIS:
        r = httpx.get(_COMPATIVEIS[brain] + "/models", headers={"authorization": f"Bearer {key}"}, timeout=10)
    elif brain == "ollama":
        r = httpx.get(settings.ollama_url.rstrip("/") + "/api/tags", timeout=5)
    else:
        raise BrainError("Cérebro desconhecido.")
    if r.status_code != 200:
        raise BrainError(f"{LABELS[brain]} respondeu HTTP {r.status_code} ao listar modelos.")
    return filter_models(brain, r.json())


__all__ = ["Brain", "BrainError", "KEY_LINKS", "LABELS", "create_brain", "filter_models", "list_models",
           "validate_key"]
