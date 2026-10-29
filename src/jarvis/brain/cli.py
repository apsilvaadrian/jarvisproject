"""Cérebros que usam a SUA conta (assinatura) pelas CLIs oficiais: Claude Code, Codex e Gemini CLI.

As CLIs são agentes de programação com ferramentas próprias (terminal, arquivos). Aqui elas rodam
SEM essas ferramentas (ou em modo somente leitura) numa pasta temporária vazia: quem age no PC é
só o JARVIS, com as mesmas confirmações de sempre. As ferramentas do JARVIS são ensinadas por texto:
para agir, o modelo responde <ferramenta>{"nome": ..., "args": {...}}</ferramenta>.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .. import contas
from ..config import settings
from ..tools import TOOLS, ToolExecutor
from .base import MAX_TOOL_ROUNDS, Brain, BrainError, system_prompt, tool_result_text

TIMEOUT = 150
# Os modelos às vezes "traduzem" ou trocam o nome da tag; aceitamos as variações comuns
_TAG = re.compile(r"<(ferramenta|fun[cç][aã]o|tool|tool_call|function|acao|ação)>\s*(\{.*?\})\s*</\1>", re.S | re.I)


def _tool_catalog() -> str:
    linhas = []
    for t in TOOLS:
        props = t.parameters.get("properties", {})
        req = set(t.parameters.get("required", []))
        params = []
        for nome, p in props.items():
            extra = f" opções: {'|'.join(p['enum'])}" if "enum" in p else ""
            params.append(f"{nome}{'' if nome in req else '?'}: {p.get('type')} — {p.get('description', '')}{extra}")
        aviso = " [pede confirmação do usuário]" if t.confirm else ""
        linhas.append(f"- {t.name}({'; '.join(params)}) — {t.description}{aviso}")
    return "\n".join(linhas)


def instructions() -> str:
    return system_prompt() + f"""

FERRAMENTAS DO COMPUTADOR (você não executa nada diretamente; o JARVIS executa por você)
Para usar uma ferramenta, responda SOMENTE com uma ou mais linhas neste formato EXATO (a tag se chama
literalmente "ferramenta", não traduza nem mude; o nome é copiado da lista, com sublinhados):
<ferramenta>{{"nome": "status_sistema", "args": {{}}}}</ferramenta>
<ferramenta>{{"nome": "abrir_aplicativo", "args": {{"nome": "spotify"}}}}</ferramenta>
Depois o JARVIS mostra os resultados em [RESULTADOS DAS FERRAMENTAS]; aí responda ao usuário em texto
normal, sem tags. Dados do PC (memória, CPU, processos, disco...) SEMPRE vêm de uma ferramenta.
Nunca use outras ferramentas, comandos de terminal, leitura de arquivos ou buscas por conta própria.

Ferramentas disponíveis:
{_tool_catalog()}"""


def _transcript(history: list[dict[str, str]], resultados: list[tuple[str, dict, str]]) -> str:
    partes = ["[CONVERSA]"]
    for m in history:
        quem = "Usuário" if m["role"] == "user" else "JARVIS"
        partes.append(f"{quem}: {m['text']}")
    if resultados:
        partes.append("\n[RESULTADOS DAS FERRAMENTAS QUE VOCÊ PEDIU NESTA RESPOSTA — não peça de novo]")
        for nome, args, res in resultados:
            partes.append(f"- {nome}({json.dumps(args, ensure_ascii=False)}) -> {res}")
    partes.append("\nJARVIS:")
    return "\n".join(partes)


_NOMES = {t.name.replace("_", ""): t.name for t in TOOLS}


def parse_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    calls = []
    for _tag, raw in _TAG.findall(text or ""):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        nome = data.get("nome") or data.get("name") or data.get("ferramenta") or data.get("tool")
        args = data.get("args") or data.get("arguments") or data.get("parametros") or {}
        if nome:
            nome = str(nome).strip()
            nome = _NOMES.get(nome.replace("_", ""), nome)   # "statussistema" -> "status_sistema"
            calls.append((nome, args if isinstance(args, dict) else {}))
    return calls


def clean_reply(text: str) -> str:
    text = _TAG.sub("", text or "")
    # Blocos de código (```...```) ficam: a tela mostra formatado e a voz não lê. Fora deles, tira o markdown.
    partes = re.split(r"(```.*?```)", text, flags=re.S)
    partes = [p if p.startswith("```") else re.sub(r"\*\*|__|^#+\s*|`", "", p, flags=re.M) for p in partes]
    text = "".join(partes)
    return text.strip()


class CliBrain(Brain):
    """Base: monta o prompt, chama a CLI e executa as ferramentas pedidas."""

    conta_id = ""

    def __init__(self) -> None:
        self.cmd = contas.comando(self.conta_id)
        if not self.cmd:
            raise BrainError(f"{contas.CONTAS[self.conta_id].nome}: CLI não instalada.")
        self.workdir = Path(tempfile.gettempdir()) / "jarvis_cli"    # pasta vazia: nada para a CLI mexer
        self.workdir.mkdir(exist_ok=True)

    def _run(self, args: list[str], stdin: str) -> subprocess.CompletedProcess:
        env = dict(contas.cli_env(), NO_COLOR="1", CI="1")
        try:
            return subprocess.run(self.cmd + args, input=stdin, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=TIMEOUT, cwd=self.workdir, env=env,
                                  creationflags=contas.NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise BrainError(f"{contas.CONTAS[self.conta_id].nome} demorou demais para responder.")
        except OSError as exc:
            raise BrainError(f"não consegui executar a CLI ({exc})")

    def ask(self, prompt: str) -> str:
        raise NotImplementedError

    def respond(self, history: list[dict[str, str]], executor: ToolExecutor) -> str:
        hist = self.trimmed(history)
        resultados: list[tuple[str, dict, str]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            reply = self.ask(_transcript(hist, resultados))
            calls = parse_tool_calls(reply)
            if not calls:
                return clean_reply(reply)
            for nome, args in calls:
                resultados.append((nome, args, tool_result_text(executor.run(nome, args))))
        return "Executei as ações solicitadas, " + settings.tratamento + "."


def _login_error(texto: str, conta_id: str) -> BrainError:
    nome = contas.CONTAS[conta_id].nome
    t = texto.lower()
    if "no longer supported" in t or "antigravity" in t:
        return BrainError(f"{nome}: o Google encerrou esse login para contas pessoais — use a chave de API "
                          "gratuita do Gemini em ⚙ → Cérebros por chave de API")
    if any(k in t for k in ("login", "log in", "not logged", "unauthorized", "authenticate", "auth method", "api key")):
        return BrainError(f"{nome}: conta não conectada — use ⚙ → Contas → Conectar")
    if any(k in t for k in ("usage limit", "rate limit", "limit reached", "quota", "429")):
        return BrainError(f"{nome}: limite de uso do seu plano atingido")
    return BrainError(f"{nome}: {texto.strip()[:300] or 'erro desconhecido'}")


class ClaudeContaBrain(CliBrain):
    name = "claude-conta"
    conta_id = "claude-conta"

    def ask(self, prompt: str) -> str:
        sysfile = self.workdir / "jarvis_sistema.txt"
        sysfile.write_text(instructions(), encoding="utf-8")
        # --tools "" desliga TODAS as ferramentas do Claude Code; o prompt de sistema substitui o padrão
        args = ["-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
                "--no-session-persistence", "--system-prompt-file", str(sysfile)]
        if settings.claude_conta_model:
            args += ["--model", settings.claude_conta_model]
        out = self._run(args, prompt)
        try:
            data = json.loads(out.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            raise _login_error(out.stderr or out.stdout, self.conta_id)
        if data.get("is_error"):
            raise _login_error(str(data.get("result", "")), self.conta_id)
        return str(data.get("result", ""))


class CodexContaBrain(CliBrain):
    name = "openai-conta"
    conta_id = "openai-conta"

    def ask(self, prompt: str) -> str:
        saida = self.workdir / "codex_resposta.txt"
        saida.unlink(missing_ok=True)
        # Somente leitura, sem repositório, sem sessão salva; esforço de raciocínio baixo = resposta rápida
        args = ["exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "-C", str(self.workdir),
                "-c", 'model_reasoning_effort="low"', "-o", str(saida), "-"]
        if settings.openai_conta_model:
            args[1:1] = ["-m", settings.openai_conta_model]
        out = self._run(args, instructions() + "\n\n" + prompt)
        if out.returncode != 0 or not saida.is_file():
            raise _login_error(out.stderr or out.stdout, self.conta_id)
        return saida.read_text(encoding="utf-8", errors="replace")


class GeminiContaBrain(CliBrain):
    name = "gemini-conta"
    conta_id = "gemini-conta"

    def ask(self, prompt: str) -> str:
        # Modo "plan" = somente leitura; o texto completo vai pela entrada padrão
        args = ["-p", "Siga as instruções e a conversa recebidas acima.", "-o", "json",
                "--approval-mode", "plan", "--skip-trust"]
        if settings.gemini_conta_model:
            args += ["-m", settings.gemini_conta_model]
        out = self._run(args, instructions() + "\n\n" + prompt)
        try:
            data = json.loads(out.stdout[out.stdout.find("{"):])
        except (json.JSONDecodeError, ValueError):
            raise _login_error(out.stderr or out.stdout, self.conta_id)
        if data.get("error"):
            raise _login_error(json.dumps(data["error"], ensure_ascii=False), self.conta_id)
        return str(data.get("response", ""))
