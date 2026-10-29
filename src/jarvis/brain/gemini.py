"""Cérebro Gemini (Google Generative Language API com function calling).

Resiliência: os modelos do Gemini às vezes ficam sobrecarregados (HTTP 503), atingem o
limite gratuito por minuto (429) ou são aposentados (404). O JARVIS então:
  1. espera e repete quando o Google informa que o limite libera em poucos segundos;
  2. tenta os modelos reserva — inclusive no meio de uma resposta que já usou ferramentas,
     passando os resultados já obtidos para o novo modelo (sem executar a ação de novo);
  3. só depois disso desiste e o assistente avisa o motivo.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from ..config import settings
from ..logger import log
from ..tools import ToolExecutor
from .base import MAX_TOOL_ROUNDS, Brain, BrainError, system_prompt, tool_result_text

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Reservas usadas só se o modelo escolhido falhar. Os "Lite" têm cota diária gratuita bem maior
# (o 3.5 Flash permite só 20 pedidos/dia no plano gratuito, out/2026)
FALLBACK_MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash"]
RETRYABLE = {429, 500, 503, 504}
REQUEST_TIMEOUT = 20
COOLDOWN = 5 * 60    # segundos evitando um modelo depois de sobrecarga/timeout
MAX_WAIT = 15        # espera no máximo isso quando o Google pede para tentar de novo


class TemporaryBrainError(BrainError):
    """Falha passageira (sobrecarga, limite por minuto, timeout): vale tentar outro modelo."""


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini não aceita 'required' vazio nem objetos sem propriedades."""
    out = dict(schema)
    if not out.get("required"):
        out.pop("required", None)
    return out


def _retry_delay(error: dict[str, Any]) -> float | None:
    """Lê o 'retryDelay' (ex.: '7s') que o Google envia junto com o 429."""
    for d in error.get("details", []):
        if d.get("@type", "").endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


class GeminiBrain(Brain):
    name = "gemini"

    def __init__(self) -> None:
        if not settings.gemini_key:
            raise BrainError("GEMINI_API_KEY não configurada.")
        self.http = httpx.Client(timeout=REQUEST_TIMEOUT, headers={"x-goog-api-key": settings.gemini_key})
        decls = []
        for t in self.tool_specs():
            d = {"name": t["name"], "description": t["description"]}
            if t["parameters"]["properties"]:
                d["parameters"] = _gemini_schema(t["parameters"])
            decls.append(d)
        self.tools = [{"functionDeclarations": decls}]
        self.no_thinking_cfg: set[str] = set()   # modelos que recusaram thinkingConfig
        self.cooldown: dict[str, float] = {}     # modelo -> até quando evitá-lo (falhou há pouco)
        self.last_model = ""

    def _post(self, model: str, body: dict[str, Any]) -> httpx.Response:
        payload = dict(body)
        # Pensamento mínimo: resposta em ~2 s em vez de ~20 s (ideal para conversa por voz)
        if settings.gemini_thinking and model not in self.no_thinking_cfg:
            payload["generationConfig"] = {"thinkingConfig": {"thinkingLevel": settings.gemini_thinking}}
        r = self.http.post(API_URL.format(model=model), json=payload)
        if r.status_code == 400 and "thinking" in r.text.lower() and "generationConfig" in payload:
            self.no_thinking_cfg.add(model)       # modelo não suporta: tenta de novo sem a opção
            r = self.http.post(API_URL.format(model=model), json=body)
        return r

    def _generate(self, models: list[str], body: dict[str, Any], executor: ToolExecutor) -> tuple[str, dict]:
        """Tenta cada modelo da lista até um responder. Retorna (modelo usado, json da resposta)."""
        erros = []
        # Modelos que falharam há pouco (ou estouraram a cota do dia) são pulados; só voltam a ser
        # tentados se TODOS estiverem nessa situação
        now = time.monotonic()
        livres = [m for m in models if self.cooldown.get(m, 0) <= now]
        models = livres or sorted(models, key=lambda m: self.cooldown.get(m, 0))
        for model in models:
            waited = False
            while True:
                try:
                    r = self._post(model, body)
                except httpx.TimeoutException:
                    erros.append(f"{model}: demorou demais")
                    self.cooldown[model] = time.monotonic() + COOLDOWN
                    break
                except httpx.HTTPError as exc:
                    raise TemporaryBrainError(f"sem conexão com o Gemini ({exc.__class__.__name__})")
                if r.status_code == 200:
                    self.cooldown.pop(model, None)
                    # Avisa só quando muda de modelo (não a cada pergunta)
                    if model != settings.gemini_model and model != self.last_model:
                        executor.emit({"type": "warning",
                                       "mensagem": f"{settings.gemini_model} indisponível; respondendo com {model}."})
                    self.last_model = model
                    return model, r.json()
                error = r.json().get("error", {}) if r.content else {}
                msg = error.get("message", r.text[:200])
                log.warning("Gemini %s respondeu %s: %s", model, r.status_code, msg[:300])
                delay = _retry_delay(error) if r.status_code == 429 else None
                # Limite por minuto que libera logo: espera e repete o MESMO modelo uma vez
                if delay is not None and delay <= MAX_WAIT and not waited:
                    executor.emit({"type": "warning",
                                   "mensagem": f"Limite por minuto do Gemini atingido; aguardando {delay:.0f} s."})
                    time.sleep(delay + 0.5)
                    waited = True
                    continue
                retire = r.status_code == 404 or "no longer available" in msg
                if r.status_code not in RETRYABLE and not retire:
                    raise BrainError(f"Gemini respondeu {r.status_code}: {msg[:300]}")
                erros.append(f"{model}: HTTP {r.status_code}")
                if retire:
                    pausa = 24 * 3600
                elif delay is not None and delay > MAX_WAIT:
                    pausa = delay          # cota do dia esgotada: o Google diz quando libera
                else:
                    pausa = COOLDOWN
                self.cooldown[model] = time.monotonic() + pausa
                time.sleep(0.4)
                break
        raise TemporaryBrainError("todos os modelos Gemini falharam (" + "; ".join(erros) + ")")

    def _body(self, contents: list[dict[str, Any]]) -> dict[str, Any]:
        return {"systemInstruction": {"parts": [{"text": system_prompt()}]}, "contents": contents, "tools": self.tools}

    @staticmethod
    def _with_tool_results(base: list[dict[str, Any]], tool_log: list[tuple[str, dict, str]]) -> list[dict[str, Any]]:
        """Contexto para trocar de modelo no meio da resposta: as ações já feitas viram texto,
        assim o novo modelo responde com os resultados sem executar a ação de novo."""
        resumo = "\n".join(f"- {n}({json.dumps(a, ensure_ascii=False)}) -> {res[:1500]}" for n, a, res in tool_log)
        contents = [dict(c, parts=list(c["parts"])) for c in base]
        contents[-1]["parts"].append({"text": "[sistema] Ferramentas já executadas neste pedido (NÃO execute de novo):\n"
                                              f"{resumo}\nResponda ao usuário com base nesses resultados."})
        return contents

    def respond(self, history: list[dict[str, str]], executor: ToolExecutor) -> str:
        base: list[dict[str, Any]] = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["text"]}]}
            for m in self.trimmed(history)
        ]
        contents = list(base)
        all_models = list(dict.fromkeys([settings.gemini_model, *FALLBACK_MODELS]))
        current = ""                                  # modelo que está conduzindo esta resposta
        tool_log: list[tuple[str, dict, str]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                # As "thought signatures" do Gemini só valem para o mesmo modelo: dentro de uma
                # resposta ficamos nele enquanto ele responder
                model, data = self._generate([current] if current else all_models, self._body(contents), executor)
            except TemporaryBrainError:
                if not current:
                    raise
                others = [m for m in all_models if m != current]
                contents = self._with_tool_results(base, tool_log)
                model, data = self._generate(others, self._body(contents), executor)
            current = model
            candidates = data.get("candidates") or []
            if not candidates:
                return "Receio não ter uma resposta para isso, " + settings.tratamento + "."
            content = candidates[0].get("content") or {"role": "model", "parts": []}
            # Reenviamos o conteúdo como veio (inclui thoughtSignature dos modelos 2.5+)
            contents.append(content)
            parts = content.get("parts", [])
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            responses = []
            for call in calls:
                args = call.get("args") or {}
                result = tool_result_text(executor.run(call["name"], args))
                tool_log.append((call["name"], args, result))
                responses.append({"functionResponse": {"name": call["name"], "response": {"resultado": result}}})
            contents.append({"role": "user", "parts": responses})
        return "Executei as ações solicitadas, " + settings.tratamento + "."
