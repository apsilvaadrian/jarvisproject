"""Controle do sistema operacional: abrir apps, processos, mídia/volume, arquivos, energia.

Funciona no Windows e no Linux. Ações destrutivas (encerrar processo, desligar)
NUNCA são executadas direto daqui pelo cérebro: passam pela confirmação em tools.py.
"""
from __future__ import annotations

import os
import re
import shutil
import unicodedata
import subprocess
import time
import urllib.parse
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from .config import settings

IS_WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Processos que o JARVIS se recusa a encerrar (derrubariam o sistema ou a sessão).
PROTECTED_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "memory compression",
    "systemd", "init", "kthreadd", "xorg", "gnome-shell", "sshd", "dbus-daemon", "plasmashell",
}

# Apelidos em português -> comando/URI por plataforma
APP_ALIASES: dict[str, dict[str, str]] = {
    "spotify": {"nt": "spotify:", "posix": "spotify"},
    "navegador": {"nt": "https://www.google.com", "posix": "xdg-open https://www.google.com"},
    "chrome": {"nt": "chrome", "posix": "google-chrome"},
    "edge": {"nt": "msedge", "posix": "microsoft-edge"},
    "firefox": {"nt": "firefox", "posix": "firefox"},
    "bloco de notas": {"nt": "notepad", "posix": "gedit"},
    "notepad": {"nt": "notepad", "posix": "gedit"},
    "calculadora": {"nt": "calc", "posix": "gnome-calculator"},
    "explorador": {"nt": "explorer", "posix": "xdg-open ~"},
    "arquivos": {"nt": "explorer", "posix": "xdg-open ~"},
    "terminal": {"nt": "wt", "posix": "x-terminal-emulator"},
    "prompt": {"nt": "cmd", "posix": "x-terminal-emulator"},
    "powershell": {"nt": "powershell", "posix": "pwsh"},
    "vscode": {"nt": "code", "posix": "code"},
    "vs code": {"nt": "code", "posix": "code"},
    "visual studio code": {"nt": "code", "posix": "code"},
    "discord": {"nt": "discord:", "posix": "discord"},
    "steam": {"nt": "steam:", "posix": "steam"},
    "whatsapp": {"nt": "whatsapp:", "posix": "xdg-open https://web.whatsapp.com"},
    "configurações": {"nt": "ms-settings:", "posix": "gnome-control-center"},
    "configuracoes": {"nt": "ms-settings:", "posix": "gnome-control-center"},
    "gerenciador de tarefas": {"nt": "taskmgr", "posix": "gnome-system-monitor"},
    "paint": {"nt": "mspaint", "posix": "pinta"},
    "word": {"nt": "winword", "posix": "libreoffice --writer"},
    "excel": {"nt": "excel", "posix": "libreoffice --calc"},
    "youtube": {"nt": "https://www.youtube.com", "posix": "xdg-open https://www.youtube.com"},
}


WEB_VERSIONS = {
    "spotify": "https://open.spotify.com", "whatsapp": "https://web.whatsapp.com", "discord": "https://discord.com/app",
    "teams": "https://teams.microsoft.com", "word": "https://www.office.com/launch/word",
    "excel": "https://www.office.com/launch/excel", "vscode": "https://vscode.dev", "vs code": "https://vscode.dev",
    "visual studio code": "https://vscode.dev",
}


def _start_menu_shortcuts() -> list[Path]:
    dirs = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path.home() / "Desktop",
    ]
    found: list[Path] = []
    for d in dirs:
        if d.is_dir():
            found.extend(d.rglob("*.lnk"))
            found.extend(d.rglob("*.url"))
    return found


_APPS_INICIAR: dict[str, Any] = {"em": 0.0, "lista": []}


def _apps_menu_iniciar() -> list[tuple[str, str]]:
    """Apps do Menu Iniciar, inclusive os da Microsoft Store/MSIX (ex.: Claude, ChatGPT/Codex), que não têm atalho
    .lnk: (nome, AppID). Guardado por 5 min."""
    import json
    import time
    if _APPS_INICIAR["lista"] and time.monotonic() - _APPS_INICIAR["em"] < 300:
        return _APPS_INICIAR["lista"]
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                           capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        dados = json.loads(r.stdout.decode("utf-8", "ignore") or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return _APPS_INICIAR["lista"]
    if isinstance(dados, dict):
        dados = [dados]
    _APPS_INICIAR["lista"] = [(d["Name"], d["AppID"]) for d in dados if isinstance(d, dict) and d.get("Name") and d.get("AppID")]
    _APPS_INICIAR["em"] = time.monotonic()
    return _APPS_INICIAR["lista"]


def _app_menu_iniciar(key: str) -> tuple[str, str] | None:
    """O app do Menu Iniciar que melhor bate com o nome: igual > começa com > contém > pelo identificador
    (ex.: "codex" -> ChatGPT, cujo pacote é OpenAI.Codex)."""
    k = normalize_text(key).strip()
    if len(k) < 3:
        return None
    apps = [(n, i) for n, i in _apps_menu_iniciar() if not re.search(r"uninstall|desinstalar", n, re.I)]
    for bate in (lambda n, i: normalize_text(n) == k, lambda n, i: normalize_text(n).startswith(k),
                 lambda n, i: k in normalize_text(n), lambda n, i: k.replace(" ", "") in i.lower()):
        achados = [(n, i) for n, i in apps if bate(n, i)]
        if achados:
            return sorted(achados, key=lambda a: len(a[0]))[0]
    return None


def _linux_desktop_entries() -> list[Path]:
    dirs = [Path("/usr/share/applications"), Path("/var/lib/snapd/desktop/applications"),
            Path("/var/lib/flatpak/exports/share/applications"), Path.home() / ".local/share/applications"]
    return [f for d in dirs if d.is_dir() for f in d.glob("*.desktop")]


def _spawn(cmd: list[str] | str, shell: bool = False) -> None:
    """Cria um processo filho desacoplado do JARVIS (não bloqueia, não herda o console)."""
    # Sempre na pasta do usuário (não na pasta do JARVIS, que ao iniciar com o Windows pode ser a System32)
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": shell, "cwd": str(Path.home())}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def _launch_target(target: str) -> None:
    if IS_WINDOWS:
        if target.endswith(":") or "://" in target or Path(target).exists():
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            # "start" consulta o PATH e o registro (App Paths), como o Executar (Win+R)
            _spawn(f'start "" {target}', shell=True)
    else:
        _spawn(os.path.expanduser(target), shell=True)


def protocol_registered(scheme: str) -> bool:
    """Há um app registrado para links 'scheme:'? Sem app, o Windows não dá erro ao abrir o link —
    mostra "Escolher um aplicativo"/Microsoft Store —, então conferimos no registro antes."""
    if not IS_WINDOWS:
        return True
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme) as key:
            winreg.QueryValueEx(key, "URL Protocol")
        return True
    except OSError:
        return scheme.startswith("ms-")      # protocolos do próprio Windows (ms-settings:) nem sempre têm a marca


def _app_path_exists(exe: str) -> bool:
    """Programas registrados em 'App Paths' (o que o Win+R encontra, ex.: chrome, winword, excel)."""
    import winreg
    nome = exe if exe.lower().endswith(".exe") else exe + ".exe"
    for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            winreg.CloseKey(winreg.OpenKey(raiz, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{nome}"))
            return True
        except OSError:
            continue
    return False


def normalize_text(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _mhnet_notifier() -> Path | None:
    """O MHnet Notifier é instalado em %APPDATA%\\MHnet Notifier, sem atalho no Menu Iniciar."""
    casa = Path.home()
    candidatos = [Path(os.environ.get("APPDATA", "")) / "MHnet Notifier" / "MHnetNotifierLauncher.exe"]
    for pasta in (casa / "Desktop", casa / "OneDrive" / "Desktop", casa / "Downloads"):
        if pasta.is_dir():
            candidatos += sorted(pasta.glob("MHnetNotifier*/MHnetNotifierLauncher.exe"), reverse=True)
    return next((c for c in candidatos if c.is_file()), None)


def _user_exe(key: str) -> Path | None:
    """Programas "portáteis" do usuário (AppData), que não aparecem no Menu Iniciar."""
    alvo = key.replace(" ", "")
    if len(alvo) < 4:
        return None
    bases = [Path(os.environ[v]) for v in ("APPDATA", "LOCALAPPDATA") if os.environ.get(v)]
    bases += [b / "Programs" for b in bases]
    for base in bases:
        try:
            pastas = [d for d in base.iterdir() if d.is_dir() and alvo in d.name.lower().replace(" ", "")]
        except OSError:
            continue
        for pasta in pastas:
            exes = [e for e in list(pasta.glob("*.exe")) + list(pasta.glob("*/*.exe"))
                    if not re.search(r"unins|uninstall|update|crash|helper", e.name, re.I)]
            if exes:
                return sorted(exes, key=lambda e: (alvo not in e.stem.lower().replace(" ", ""), len(e.parts)))[0]
    return None


def _work_app(key: str) -> dict[str, Any] | None:
    """Apps do trabalho do usuário: MHnet Notifier (massivas) e StatusRobot (status report)."""
    k = re.sub(r"[^a-z0-9 ]", "", key)
    if "notifier" in k or "massiva" in k or k in ("mhnet", "app mhnet"):
        exe = _mhnet_notifier()
        if exe is None:
            return {"ok": False, "mensagem": "Não encontrei o MHnet Notifier neste PC (procurei em "
                                             "%APPDATA%\\MHnet Notifier e na Área de Trabalho)."}
        _launch_target(str(exe))
        return {"ok": True, "mensagem": "Abrindo o MHnet Notifier.", "alvo": str(exe)}
    if "statusrobot" in k.replace(" ", "") or "status report" in k or "robo" in k:
        from . import trabalho
        return trabalho.executar("agora")
    return None


def open_app(nome: str) -> dict[str, Any]:
    """Abre um aplicativo pelo nome ou apelido."""
    key = nome.strip().lower()
    key = re.sub(r"^(o |a )?(app|aplicativo|programa)\s+(d[aeo]s?\s+)?", "", key).strip() or key
    plat = "nt" if IS_WINDOWS else "posix"
    if IS_WINDOWS:
        trabalho_app = _work_app(normalize_text(key))
        if trabalho_app is not None:
            return trabalho_app

    if key in APP_ALIASES:
        target = APP_ALIASES[key][plat]
        exe = target.split()[0]
        # No Windows, "start programa" com programa inexistente abre uma janela de erro em vez de falhar;
        # por isso só usamos o atalho se o programa existir (PATH ou App Paths do registro) ou for um link
        if IS_WINDOWS:
            if "://" in target:
                existe = True                                    # site: sempre abre no navegador
            elif target.endswith(":"):
                existe = protocol_registered(target[:-1])        # spotify:, discord:, steam:, ms-settings:
            else:
                existe = bool(shutil.which(exe) or _app_path_exists(exe))
        else:
            existe = bool(shutil.which(exe) or exe == "xdg-open")
        if existe:
            try:
                _launch_target(target)
                return {"ok": True, "mensagem": f"Abrindo {nome}.", "alvo": target}
            except OSError:
                pass  # tenta as buscas abaixo

    if IS_WINDOWS:
        matches = [p for p in _start_menu_shortcuts() if key in p.stem.lower()]
        # Prefere o nome mais curto (ex.: "Spotify" em vez de "Desinstalar Spotify")
        matches = sorted((m for m in matches if "uninstall" not in m.stem.lower()
                          and "desinstalar" not in m.stem.lower()), key=lambda p: len(p.stem))
        if matches:
            os.startfile(str(matches[0]))  # type: ignore[attr-defined]
            return {"ok": True, "mensagem": f"Abrindo {matches[0].stem}.", "alvo": str(matches[0])}
        # Apps da Microsoft Store/MSIX (Claude, ChatGPT/Codex, WhatsApp...) antes de qualquer comando de terminal
        app = _app_menu_iniciar(key)
        if app is not None:
            _spawn(["explorer.exe", "shell:AppsFolder\\" + app[1]])
            return {"ok": True, "mensagem": f"Abrindo {app[0]}.", "alvo": app[1]}
        exe = _user_exe(key)
        if exe is not None:
            _launch_target(str(exe))
            return {"ok": True, "mensagem": f"Abrindo {exe.stem}.", "alvo": str(exe)}
        if shutil.which(key) or shutil.which(key + ".exe"):
            _launch_target(key)
            return {"ok": True, "mensagem": f"Abrindo {nome}.", "alvo": key}
        exe = _user_exe(key)
        if exe is not None:
            _launch_target(str(exe))
            return {"ok": True, "mensagem": f"Abrindo {exe.stem}.", "alvo": str(exe)}
    else:
        if shutil.which(key):
            _spawn([key])
            return {"ok": True, "mensagem": f"Abrindo {nome}.", "alvo": key}
        for entry in _linux_desktop_entries():
            text = entry.read_text(errors="ignore")
            name_line = next((ln for ln in text.splitlines() if ln.startswith("Name=")), "")
            if key in entry.stem.lower() or key in name_line.lower():
                launcher = shutil.which("gtk-launch") or shutil.which("gio")
                if launcher and launcher.endswith("gtk-launch"):
                    _spawn([launcher, entry.stem])
                elif launcher:
                    _spawn([launcher, "launch", str(entry)])
                else:
                    continue
                return {"ok": True, "mensagem": f"Abrindo {name_line[5:] or entry.stem}.", "alvo": str(entry)}

    # App não instalado (ex.: PC do trabalho): usa a versão web quando existe uma
    if key in WEB_VERSIONS:
        webbrowser.open(WEB_VERSIONS[key])
        return {"ok": True, "mensagem": f"{nome.capitalize()} não está instalado aqui; abri a versão web.",
                "alvo": WEB_VERSIONS[key]}
    return {"ok": False, "mensagem": f"Não encontrei o aplicativo '{nome}' neste computador."}


def open_website(url: str) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return {"ok": True, "mensagem": f"Abrindo {url}."}


def web_search(consulta: str, site: str = "google") -> dict[str, Any]:
    q = urllib.parse.quote_plus(consulta)
    urls = {
        "google": f"https://www.google.com/search?q={q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "wikipedia": f"https://pt.wikipedia.org/w/index.php?search={q}",
    }
    webbrowser.open(urls.get(site, urls["google"]))
    return {"ok": True, "mensagem": f"Pesquisando '{consulta}' no {site}."}


# --------------------------------------------------------------------------- processos
def find_processes(alvo: str | int) -> list[psutil.Process]:
    if isinstance(alvo, int) or str(alvo).isdigit():
        try:
            return [psutil.Process(int(alvo))]
        except psutil.NoSuchProcess:
            return []
    name = str(alvo).lower().removesuffix(".exe")
    out = []
    for p in psutil.process_iter(["name"]):
        pname = (p.info["name"] or "").lower()
        if pname.removesuffix(".exe") == name:
            out.append(p)
    return out


def is_protected(proc: psutil.Process) -> bool:
    try:
        name = (proc.name() or "").lower()
    except psutil.Error:
        return True
    return proc.pid <= 4 or proc.pid == os.getpid() or name in PROTECTED_PROCESSES


def describe_kill(alvo: str | int) -> dict[str, Any]:
    """Pré-visualização de quais processos seriam encerrados (sem encerrar)."""
    procs = find_processes(alvo)
    if not procs:
        return {"ok": False, "mensagem": f"Nenhum processo encontrado para '{alvo}'."}
    protegidos = [p for p in procs if is_protected(p)]
    if protegidos:
        return {"ok": False, "mensagem": f"'{alvo}' é um processo crítico do sistema; não vou encerrá-lo."}
    total_mb = 0.0
    for p in procs:
        try:
            total_mb += p.memory_info().rss / 1024**2
        except psutil.Error:
            pass
    return {"ok": True, "pids": [p.pid for p in procs], "quantidade": len(procs),
            "memoria_mb": round(total_mb, 1), "nome": procs[0].name()}


def kill_process(alvo: str | int, timeout: float = 3.0) -> dict[str, Any]:
    """Encerramento controlado: SIGTERM (terminate) e, se não responder, SIGKILL (kill)."""
    preview = describe_kill(alvo)
    if not preview["ok"]:
        return preview
    procs = find_processes(alvo)
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    return {"ok": True, "mensagem": f"{len(procs)} processo(s) '{preview['nome']}' encerrado(s).",
            "encerrados_gracioso": len(gone), "forcados": len(alive)}


# --------------------------------------------------------------------------- mídia e volume
_VK = {"play_pause": 0xB3, "proxima": 0xB0, "anterior": 0xB1, "parar": 0xB2,
       "volume_mais": 0xAF, "volume_menos": 0xAE, "mudo": 0xAD}
_LINUX_MEDIA = {
    "play_pause": ["playerctl", "play-pause"], "proxima": ["playerctl", "next"],
    "anterior": ["playerctl", "previous"], "parar": ["playerctl", "stop"],
    "volume_mais": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
    "volume_menos": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
    "mudo": ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
}


def _press_vk(code: int, times: int = 1) -> None:
    import ctypes
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(times):
        user32.keybd_event(code, 0, 0, 0)       # tecla pressionada
        user32.keybd_event(code, 0, 2, 0)       # KEYEVENTF_KEYUP
        time.sleep(0.01)


def media_control(acao: str) -> dict[str, Any]:
    if acao not in _VK:
        return {"ok": False, "mensagem": f"Ação de mídia desconhecida: {acao}"}
    if IS_WINDOWS:
        _press_vk(_VK[acao], times=5 if acao.startswith("volume") else 1)
    else:
        cmd = _LINUX_MEDIA[acao]
        if not shutil.which(cmd[0]):
            return {"ok": False, "mensagem": f"Instale '{cmd[0]}' para controlar mídia no Linux."}
        subprocess.run(cmd, capture_output=True, timeout=3)
    return {"ok": True, "mensagem": f"Mídia: {acao.replace('_', ' ')}."}


def set_volume(percent: int) -> dict[str, Any]:
    percent = max(0, min(100, int(percent)))
    if IS_WINDOWS:
        # Sem dependências extras: zera e sobe em passos de 2% (cada tecla = 2%)
        _press_vk(_VK["volume_menos"], 50)
        _press_vk(_VK["volume_mais"], round(percent / 2))
    else:
        if not shutil.which("pactl"):
            return {"ok": False, "mensagem": "Instale 'pactl' (pulseaudio-utils) para ajustar o volume."}
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"], capture_output=True, timeout=3)
    return {"ok": True, "mensagem": f"Volume ajustado para {percent}%."}


# --------------------------------------------------------------------------- arquivos
def analyze_directory(caminho: str = "~", top: int = 5) -> dict[str, Any]:
    """Percorre um diretório: espaço ocupado, maiores arquivos e agrupamento por extensão."""
    root = Path(os.path.expanduser(caminho)).resolve()
    if not root.is_dir():
        return {"ok": False, "mensagem": f"Diretório não encontrado: {root}"}
    total, count, negados = 0, 0, 0
    by_ext: Counter[str] = Counter()
    biggest: list[tuple[int, str]] = []
    deadline = time.monotonic() + 8  # evita travar em pastas gigantes
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "$Recycle.Bin")]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                size = os.stat(fp, follow_symlinks=False).st_size
            except (PermissionError, OSError):
                negados += 1
                continue
            total += size
            count += 1
            by_ext[Path(fn).suffix.lower() or "(sem extensão)"] += size
            biggest.append((size, fp))
            if len(biggest) > top * 20:
                biggest = sorted(biggest, reverse=True)[:top]
        if time.monotonic() > deadline:
            break
    biggest = sorted(biggest, reverse=True)[:top]
    return {
        "ok": True,
        "diretorio": str(root),
        "arquivos": count,
        "total_mb": round(total / 1024**2, 1),
        "sem_permissao": negados,
        "maiores": [{"arquivo": p, "mb": round(s / 1024**2, 1)} for s, p in biggest],
        "por_extensao_mb": {k: round(v / 1024**2, 1) for k, v in by_ext.most_common(top)},
        "parcial": time.monotonic() > deadline,
    }


def save_note(texto: str) -> dict[str, Any]:
    """Acrescenta uma nota em data/notas.md (cria o arquivo com permissão 600 no Linux)."""
    settings.ensure_dirs()
    path = settings.data_dir / "notas.md"
    new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- [{datetime.now():%d/%m/%Y %H:%M}] {texto.strip()}\n")
    if new and not IS_WINDOWS:
        os.chmod(path, 0o600)  # somente o dono lê/escreve
    return {"ok": True, "mensagem": "Nota registrada.", "arquivo": str(path)}


def read_notes(limite: int = 10) -> dict[str, Any]:
    path = settings.data_dir / "notas.md"
    if not path.exists():
        return {"ok": True, "notas": []}
    return {"ok": True, "notas": path.read_text(encoding="utf-8").splitlines()[-limite:]}


# --------------------------------------------------------------------------- energia
def lock_screen() -> dict[str, Any]:
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
    else:
        for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, capture_output=True, timeout=3)
                break
    return {"ok": True, "mensagem": "Tela bloqueada."}


def schedule_power(acao: str, minutos: int = 1) -> dict[str, Any]:
    """Agenda desligar/reiniciar (pode ser cancelado com cancel_power)."""
    segundos = max(0, int(minutos)) * 60
    if IS_WINDOWS:
        flag = "/s" if acao == "desligar" else "/r"
        subprocess.run(["shutdown", flag, "/t", str(segundos)], capture_output=True, creationflags=NO_WINDOW)
    else:
        flag = "-h" if acao == "desligar" else "-r"
        subprocess.run(["shutdown", flag, f"+{max(1, int(minutos))}"], capture_output=True)
    return {"ok": True, "mensagem": f"Computador vai {acao} em {minutos} minuto(s). Diga 'cancelar desligamento' para abortar."}


def cancel_power() -> dict[str, Any]:
    cmd = ["shutdown", "/a"] if IS_WINDOWS else ["shutdown", "-c"]
    subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW if IS_WINDOWS else 0)
    return {"ok": True, "mensagem": "Desligamento cancelado."}
