"""Contas pessoais (assinaturas) via CLIs oficiais: Claude Code, Codex (ChatGPT) e Gemini CLI.

As assinaturas (Claude Pro/Max, ChatGPT Plus, conta Google) não dão chave de API; elas valem nas
ferramentas oficiais de linha de comando de cada empresa. O JARVIS usa essas CLIs como "cérebro":
você faz login nelas uma vez (no navegador, pela própria CLI) e o JARVIS só envia o texto.

Este módulo descobre se cada CLI está instalada/logada e abre, numa janela de console visível,
a instalação (npm) ou o login.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Conta:
    id: str            # id do cérebro no JARVIS
    nome: str
    pacote: str        # pacote npm oficial
    entrada: str       # arquivo dentro do pacote que o atalho do npm executa
    nativo: bool       # True: executável nativo; False: script Node
    plano: str


CONTAS = {
    "claude-conta": Conta("claude-conta", "Claude (sua conta)", "@anthropic-ai/claude-code", "bin/claude.exe" if IS_WINDOWS else "",
                          True, "Claude Pro ou Max"),
    "openai-conta": Conta("openai-conta", "ChatGPT (sua conta)", "@openai/codex", "bin/codex.js", False,
                          "ChatGPT Plus, Pro ou Business"),
    # Out/2026: o Google encerrou o "Sign in with Google" do Gemini CLI para contas pessoais (pede para
    # migrar para o Antigravity). Continua valendo para contas Workspace/Code Assist; para uso pessoal,
    # a chave de API gratuita do Gemini (Cérebros por chave de API) é o caminho.
    "gemini-conta": Conta("gemini-conta", "Gemini (sua conta Google)", "@google/gemini-cli", "bundle/gemini.js", False,
                          "só contas de empresa (Workspace) — para conta pessoal use a chave de API grátis do Gemini"),
}
BINARIO = {"claude-conta": "claude", "openai-conta": "codex", "gemini-conta": "gemini"}


# Node.js portátil: o zip oficial extraído na pasta do usuário. Não precisa de administrador,
# serve para PCs (ex.: do trabalho) onde não dá para instalar o Node.js no sistema.
PORTABLE_NODE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "JARVIS" / "node"
_node_job = {"estado": "", "erro": ""}


def node_exe() -> str | None:
    if shutil.which("node"):
        return shutil.which("node")
    exe = PORTABLE_NODE / ("node.exe" if IS_WINDOWS else "bin/node")
    return str(exe) if exe.is_file() else None


def npm_cmd() -> str | None:
    if shutil.which("npm"):
        return shutil.which("npm")
    cmd = PORTABLE_NODE / ("npm.cmd" if IS_WINDOWS else "bin/npm")
    return str(cmd) if cmd.is_file() else None


def cli_env() -> dict[str, str]:
    """Ambiente para rodar as CLIs: inclui o Node portátil no PATH, se for o caso."""
    env = dict(os.environ)
    if PORTABLE_NODE.is_dir() and not shutil.which("node"):
        env["PATH"] = str(PORTABLE_NODE) + os.pathsep + env.get("PATH", "")
    return env


def _baixar_node_portatil() -> None:
    """Baixa a versão LTS oficial do nodejs.org (zip, ~30 MB) e extrai em PORTABLE_NODE."""
    import io
    import zipfile

    import httpx

    try:
        _node_job.update(estado="consultando a versão LTS do Node.js…", erro="")
        versoes = httpx.get("https://nodejs.org/dist/index.json", timeout=20).json()
        v = next(x["version"] for x in versoes if x.get("lts") and "win-x64-zip" in x.get("files", []))
        url = f"https://nodejs.org/dist/{v}/node-{v}-win-x64.zip"
        buf = io.BytesIO()
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            for chunk in r.iter_bytes(1 << 16):
                buf.write(chunk)
                if total:
                    _node_job["estado"] = f"baixando Node.js {v} ({buf.tell() * 100 // total}%)…"
        _node_job["estado"] = "extraindo o Node.js…"
        PORTABLE_NODE.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(buf) as z:
            raiz = f"node-{v}-win-x64/"
            for info in z.infolist():
                if info.filename.startswith(raiz) and not info.is_dir():
                    destino = PORTABLE_NODE / info.filename[len(raiz):]
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    destino.write_bytes(z.read(info))
        _node_job["estado"] = f"Node.js {v} portátil pronto"
    except Exception as exc:
        _node_job.update(estado="", erro=f"não consegui baixar o Node.js ({exc.__class__.__name__}: {str(exc)[:120]})")


def _npm_root() -> Path | None:
    if IS_WINDOWS:
        root = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules"
        return root if root.is_dir() else None
    npm = npm_cmd()
    if not npm:
        return None
    try:
        out = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=15, env=cli_env())
        return Path(out.stdout.strip()) if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def comando(conta_id: str) -> list[str] | None:
    """Linha de comando para executar a CLI sem passar pelo atalho .cmd do npm.

    No Windows os atalhos .cmd quebram argumentos com quebras de linha e caracteres especiais,
    então chamamos direto o executável nativo (Claude) ou `node script.js` (Codex/Gemini).
    """
    conta = CONTAS[conta_id]
    root = _npm_root()
    if IS_WINDOWS and root:
        alvo = root / conta.pacote / conta.entrada
        if alvo.is_file():
            if conta.nativo:
                return [str(alvo)]
            node = node_exe()
            if node:
                return [node, str(alvo)]
    exe = shutil.which(BINARIO[conta_id])
    return [exe] if exe else None


# ------------------------------------------------------------------ status (com cache)
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _logado(conta_id: str, cmd: list[str]) -> tuple[bool, str]:
    try:
        if conta_id == "claude-conta":
            out = subprocess.run(cmd + ["auth", "status", "--json"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20, creationflags=NO_WINDOW, env=cli_env())
            data = json.loads(out.stdout or "{}")
            return bool(data.get("loggedIn")), data.get("email") or data.get("authMethod") or ""
        if conta_id == "openai-conta":
            out = subprocess.run(cmd + ["login", "status"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20, creationflags=NO_WINDOW, env=cli_env())
            texto = (out.stdout + out.stderr).strip()
            return out.returncode == 0 and "logged in" in texto.lower(), texto.splitlines()[0] if texto else ""
        # Gemini CLI não tem comando de status: o login com Google grava este arquivo
        creds = Path.home() / ".gemini" / "oauth_creds.json"
        return creds.is_file(), "login com Google" if creds.is_file() else ""
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"não consegui verificar ({exc.__class__.__name__})"


def status(conta_id: str, fresh: bool = False) -> dict:
    with _lock:
        hit = _cache.get(conta_id)
        if hit and not fresh and time.monotonic() - hit[0] < 30:
            return hit[1]
    conta = CONTAS[conta_id]
    cmd = comando(conta_id)
    if not cmd:
        info = {"id": conta_id, "nome": conta.nome, "plano": conta.plano, "instalado": False, "logado": False,
                "detalhe": _node_job["estado"] or _node_job["erro"] or "CLI não instalada", "node": bool(node_exe())}
    else:
        ok, det = _logado(conta_id, cmd)
        info = {"id": conta_id, "nome": conta.nome, "plano": conta.plano, "instalado": True, "logado": ok,
                "detalhe": det, "node": True}
    with _lock:
        _cache[conta_id] = (time.monotonic(), info)
    return info


def instalada(conta_id: str) -> bool:
    """Checagem rápida (sem rodar a CLI), usada para listar os cérebros."""
    return comando(conta_id) is not None


def todas(fresh: bool = False) -> list[dict]:
    resultados: dict[str, dict] = {}
    threads = [threading.Thread(target=lambda c=c: resultados.__setitem__(c, status(c, fresh))) for c in CONTAS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [resultados[c] for c in CONTAS]


# ------------------------------------------------------------------ janelas de instalação e login
def _abrir_console(titulo: str, linhas: list[str]) -> None:
    """Abre um console visível com um script; o usuário acompanha e interage (login no navegador)."""
    if IS_WINDOWS:
        script = Path(tempfile.gettempdir()) / f"jarvis_{titulo.split()[0].lower()}_{int(time.time())}.cmd"
        script.write_text("@echo off\r\nchcp 65001 >nul\r\ntitle " + titulo + "\r\n" + "\r\n".join(linhas)
                          + "\r\necho.\r\necho Pode fechar esta janela e voltar ao JARVIS.\r\npause\r\n", encoding="utf-8")
        subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", str(script)], creationflags=NO_WINDOW)
    else:
        script = Path(tempfile.gettempdir()) / f"jarvis_{int(time.time())}.sh"
        script.write_text("#!/usr/bin/env bash\n" + "\n".join(linhas) + "\nread -p 'Enter para fechar...'\n")
        script.chmod(0o700)
        term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("konsole")
        if not term:
            raise RuntimeError("Nenhum terminal gráfico encontrado; rode manualmente: " + " && ".join(linhas))
        subprocess.Popen([term, "-e", str(script)])


def _q(parts: list[str]) -> str:
    return " ".join(f'"{p}"' if " " in p or "\\" in p else p for p in parts)


def _abrir_instalacao_npm(conta_id: str) -> None:
    conta = CONTAS[conta_id]
    npm = npm_cmd()
    linhas = [f"echo Instalando {conta.pacote} (CLI oficial) na pasta do seu usuario - sem administrador..."]
    if IS_WINDOWS:
        if not shutil.which("npm"):           # Node portátil: coloca no PATH só desta janela
            linhas.insert(0, f'set "PATH={PORTABLE_NODE};%PATH%"')
        # --prefix na pasta do usuário: funciona sem permissão de administrador
        linhas.append(f'call "{npm}" install -g --prefix "%APPDATA%\\npm" {conta.pacote}')
    else:
        linhas.append(f'"{npm}" install -g {conta.pacote}')
    _abrir_console(f"JARVIS - instalar {conta.nome}", linhas)
    _cache.pop(conta_id, None)


def abrir_instalacao(conta_id: str) -> str:
    conta = CONTAS[conta_id]
    if npm_cmd():
        _abrir_instalacao_npm(conta_id)
        return f"Abri a instalação de {conta.nome} numa janela. Quando terminar, clique em Conectar."
    if not IS_WINDOWS:
        raise RuntimeError("Instale o Node.js (https://nodejs.org) e tente de novo.")
    if _node_job["estado"].startswith(("consultando", "baixando", "extraindo")):
        return f"Já estou preparando o Node.js: {_node_job['estado']}"

    # Sem Node.js no PC: baixa a versão portátil (não precisa de administrador) e segue com a instalação
    def job() -> None:
        _baixar_node_portatil()
        if npm_cmd():
            _abrir_instalacao_npm(conta_id)

    threading.Thread(target=job, daemon=True).start()
    return ("Este PC não tem Node.js: estou baixando a versão portátil oficial (~30 MB) na sua pasta de usuário, "
            "sem precisar de administrador. Em seguida abro a instalação de " + conta.nome + ".")


def node_status() -> dict[str, str | bool]:
    return {"node": bool(node_exe()), "portatil": not shutil.which("node") and bool(node_exe()),
            "preparando": _node_job["estado"], "erro": _node_job["erro"]}


def abrir_login(conta_id: str) -> str:
    conta = CONTAS[conta_id]
    cmd = comando(conta_id)
    if not cmd:
        raise RuntimeError("Instale a CLI primeiro.")
    prefixo = [f'set "PATH={PORTABLE_NODE};%PATH%"'] if IS_WINDOWS and not shutil.which("node") else []
    if conta_id == "claude-conta":
        linhas = ["echo O navegador vai abrir para voce entrar na sua conta Claude (Pro/Max).", _q(cmd + ["auth", "login"])]
    elif conta_id == "openai-conta":
        linhas = ["echo O navegador vai abrir: escolha 'Sign in with ChatGPT'.", _q(cmd + ["login"])]
    else:
        linhas = ["echo Escolha 'Login with Google' e entre no navegador.",
                  "echo Quando aparecer a tela de conversa do Gemini, digite /quit e Enter.", _q(cmd)]
    _abrir_console(f"JARVIS - conectar {conta.nome}", prefixo + linhas)
    _cache.pop(conta_id, None)
    return f"Abri o login de {conta.nome}. Termine no navegador e clique em Atualizar."
