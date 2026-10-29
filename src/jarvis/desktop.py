"""Integração com a área de trabalho do Windows: ícone na bandeja, janela e início automático.

- Bandeja (pystray): Abrir JARVIS · Iniciar com o Windows · Sair.
- Janela: localiza a janela do JARVIS pelo título para trazer à frente ou minimizar.
- Início automático: valor "JARVIS" em HKCU\\...\\Run (não precisa de administrador).
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path
from typing import Callable

from .config import FROZEN, ROOT_DIR
from .logger import log

IS_WINDOWS = os.name == "nt"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "JARVIS"
WINDOW_TITLE = "J.A.R.V.I.S."


# --------------------------------------------------------------------------- iniciar com o Windows
# Onde o JARVIS.exe fica instalado (cópia fixa, fora de Downloads/pastas temporárias)
INSTALL_EXE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "JARVIS" / "JARVIS.exe"


def _launch_command() -> str:
    if FROZEN:
        return f'"{sys.executable}" --minimizado'
    if INSTALL_EXE.is_file():          # ligado pela versão de desenvolvimento: prefere o .exe instalado
        return f'"{INSTALL_EXE}" --minimizado'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw if pythonw.is_file() else sys.executable}" "{ROOT_DIR / "jarvis_app.py"}" --minimizado'


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
        return True
    except OSError:
        return False


def depuracao_bloqueada(exe: str) -> bool:
    """A empresa pode desligar as ferramentas de desenvolvedor por política; aí o controle do WhatsApp Web
    (protocolo de depuração) não funciona nesse navegador e o JARVIS usa o outro."""
    if not IS_WINDOWS:
        return False
    import winreg
    chave = r"SOFTWARE\Policies\Microsoft\Edge" if "msedge" in exe.lower() else r"SOFTWARE\Policies\Google\Chrome"
    for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(raiz, chave) as k:
                for nome, bloqueia in (("RemoteDebuggingAllowed", 0), ("DeveloperToolsAvailability", 2)):
                    try:
                        if winreg.QueryValueEx(k, nome)[0] == bloqueia:
                            return True
                    except OSError:
                        pass
        except OSError:
            continue
    return False


def autostart_target() -> Path | None:
    """O programa que o Windows abre ao iniciar (None se a opção estiver desligada)."""
    if not IS_WINDOWS:
        return None
    import shlex
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            valor, _ = winreg.QueryValueEx(key, RUN_NAME)
    except OSError:
        return None
    partes = shlex.split(valor, posix=False)
    return Path(partes[0].strip('"')) if partes else None


def repair_autostart() -> bool:
    """Chamado pelo JARVIS.exe ao abrir: se o início automático está ligado mas aponta para algo que não é
    um JARVIS.exe existente (versão de desenvolvimento, exe apagado ou movido), passa a apontar para este exe."""
    if not (IS_WINDOWS and FROZEN):
        return False
    alvo = autostart_target()
    if alvo is None or (alvo.is_file() and alvo.name.lower() == "jarvis.exe"):
        return False
    set_autostart(True)
    log.info("Início automático corrigido: apontava para %s, agora para %s", alvo, sys.executable)
    return True


def set_autostart(enabled: bool) -> bool:
    if not IS_WINDOWS:
        raise RuntimeError("Início automático disponível só no Windows.")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
    log.info("Iniciar com o Windows: %s", enabled)
    return autostart_enabled()


# --------------------------------------------------------------------------- janela
def _find_windows(incluir_ocultas: bool = False) -> list[int]:
    """Janelas cujo título começa com "J.A.R.V.I.S." (a interface no Edge em modo app)."""
    if not IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    found: list[int] = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length and (incluir_ocultas or user32.IsWindowVisible(hwnd)):
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if buf.value.startswith(WINDOW_TITLE) and cls.value.startswith("Chrome_WidgetWin"):
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


# Janela que escondemos na bandeja (continua rodando: ouve "Jarvis" e fala)
_oculta: dict[str, int] = {}


MODOS_JANELA = ("cheia", "maximizada", "janela")


def _tela() -> tuple[int, int]:
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def tamanho_janela() -> tuple[int, int, int, int]:
    """Modo "janela": 85% da tela, centralizada (x, y, largura, altura)."""
    sw, sh = _tela() if IS_WINDOWS else (1920, 1080)
    w, h = min(1600, int(sw * 0.85)), min(1000, int(sh * 0.85))
    return (sw - w) // 2, (sh - h) // 2, w, h


def _em_tela_cheia(hwnd: int) -> bool:
    """A janela cobre o monitor dela inteiro (inclusive a barra de tarefas)? Maximizada não cobre a barra."""
    class INFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.c_long * 4), ("rcWork", ctypes.c_long * 4),
                    ("dwFlags", ctypes.c_ulong)]
    user32 = ctypes.windll.user32
    rect = (ctypes.c_long * 4)()
    user32.GetWindowRect(ctypes.c_void_p(hwnd), rect)
    info = INFO()
    info.cbSize = ctypes.sizeof(INFO)
    mon = user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)           # MONITOR_DEFAULTTONEAREST
    if not user32.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(info)):
        return (rect[0], rect[1], rect[2], rect[3]) == (0, 0, *_tela())
    m = info.rcMonitor
    # O Chrome deixa a tela cheia 1 px menor que o monitor (1920x1079): aceita uma folga de 2 px
    return rect[0] <= m[0] + 2 and rect[1] <= m[1] + 2 and rect[2] >= m[2] - 2 and rect[3] >= m[3] - 2


def _para_frente(hwnd: int) -> bool:
    """SetForegroundWindow de um processo em segundo plano costuma ser bloqueado pelo Windows; um toque na
    tecla Alt libera a troca de foco (truque padrão de assistentes e lançadores)."""
    user32 = ctypes.windll.user32
    user32.keybd_event(0x12, 0, 0, 0)          # Alt
    user32.keybd_event(0x12, 0, 2, 0)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def _f11(hwnd: int) -> None:
    """Liga/desliga a tela cheia do Edge — só se a janela do JARVIS estiver mesmo em primeiro plano."""
    import time
    user32 = ctypes.windll.user32
    for _ in range(4):                          # o Windows às vezes nega o foco na primeira tentativa
        if user32.GetForegroundWindow() == hwnd or _para_frente(hwnd):
            break
        time.sleep(0.25)
    else:
        return
    time.sleep(0.15)
    user32.keybd_event(0x7A, 0, 0, 0)
    user32.keybd_event(0x7A, 0, 2, 0)


def aplicar_modo(hwnd: int, modo: str) -> None:
    """Deixa a janela no modo pedido: tela cheia, maximizada ou janela centralizada."""
    import time
    user32 = ctypes.windll.user32
    cheia = _em_tela_cheia(hwnd)
    if modo == "cheia":
        if not cheia:
            user32.ShowWindow(hwnd, 3)
            _f11(hwnd)
        return
    if cheia:                                   # sai da tela cheia antes de maximizar/restaurar
        _f11(hwnd)
        time.sleep(0.4)
    if modo == "janela":
        user32.ShowWindow(hwnd, 1)              # SW_SHOWNORMAL
        x, y, w, h = tamanho_janela()
        user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004)
    else:
        user32.ShowWindow(hwnd, 3)              # SW_SHOWMAXIMIZED


def _no_modo(hwnd: int, modo: str) -> bool:
    cheia = _em_tela_cheia(hwnd)
    if modo == "cheia":
        return cheia
    if modo == "maximizada":
        return not cheia and bool(ctypes.windll.user32.IsZoomed(ctypes.c_void_p(hwnd)))
    return not cheia and not ctypes.windll.user32.IsZoomed(ctypes.c_void_p(hwnd))


def _modo() -> str:
    from .config import settings
    return settings.janela if settings.janela in MODOS_JANELA else "maximizada"


def focus_window(modo: str | None = None) -> bool:
    """Mostra/restaura a janela do JARVIS e traz para a frente. False se não houver nenhuma."""
    if not IS_WINDOWS:
        return False
    user32 = ctypes.windll.user32
    modo = modo or _modo()
    hwnd = _oculta.pop("hwnd", 0)
    if not (hwnd and user32.IsWindow(hwnd)):
        hwnd = next((w for w in _find_windows() if not user32.IsIconic(w) and _fora_da_tela(w)), 0)   # oculta, mas não deu tempo de esconder
    if hwnd:
        # Estava fora da tela e escondida: volta para a área visível, no modo escolhido
        x, y, w, h = tamanho_janela()
        user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004 | 0x0010)   # SWP_NOZORDER | SWP_NOACTIVATE
        user32.ShowWindow(hwnd, 1)
        _para_frente(hwnd)
        aplicar_modo(hwnd, modo)
        return True
    wins = _find_windows()
    if not wins:
        return False
    if user32.IsIconic(wins[0]):
        user32.ShowWindow(wins[0], 9)          # SW_RESTORE
    _para_frente(wins[0])
    # Restaurar nem sempre volta como estava (a tela cheia costuma virar janela): aplica o modo escolhido
    def reaplicar() -> None:
        import time
        time.sleep(0.6)
        if not _no_modo(wins[0], modo):
            aplicar_modo(wins[0], modo)

    threading.Thread(target=reaplicar, daemon=True).start()
    return True


def janela_visivel() -> bool:
    """A janela principal está na tela (não minimizada, nem escondida na bandeja)?"""
    if not IS_WINDOWS:
        return True
    user32 = ctypes.windll.user32
    return any(not user32.IsIconic(w) and not _fora_da_tela(w) for w in _find_windows())


def ajustar_ao_abrir(timeout: float = 60.0) -> None:
    """O Edge reabre a janela do tamanho da última vez (ignora --start-maximized) e ainda a "restaura" alguns
    segundos depois de carregar; então esperamos ela assentar e aplicamos o modo escolhido."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        wins = [w for w in _find_windows() if not _fora_da_tela(w)]
        if wins:
            # Confere algumas vezes (o navegador às vezes muda a janela depois de carregar), mas só mexe se não
            # estiver no modo escolhido: apertar F11 numa janela que JÁ está em tela cheia tiraria a tela cheia
            for espera in (4, 3, 3):
                time.sleep(espera)
                wins = [w for w in _find_windows() if not _fora_da_tela(w) and not ctypes.windll.user32.IsIconic(w)]
                if wins and not _no_modo(wins[0], _modo()):
                    aplicar_modo(wins[0], _modo())
            return
        time.sleep(0.5)


def aplicar_modo_agora(modo: str) -> bool:
    """Chamado ao trocar a opção nas Configurações: aplica na janela aberta, sem reiniciar."""
    wins = [w for w in _find_windows() if not _fora_da_tela(w)] if IS_WINDOWS else []
    if not wins:
        return False
    aplicar_modo(wins[0], modo)
    return True


def _fora_da_tela(hwnd: int) -> bool:
    rect = (ctypes.c_long * 4)()
    ctypes.windll.user32.GetWindowRect(hwnd, rect)
    return rect[0] < -10000


def hide_window(timeout: float = 25.0) -> bool:
    """Esconde a janela recém-aberta fora da tela (sem ícone na barra de tarefas; só na bandeja)."""
    import time
    if not IS_WINDOWS:
        return False
    deadline = time.monotonic() + timeout
    user32 = ctypes.windll.user32
    while time.monotonic() < deadline:
        # Só a janela nova, aberta fora da tela (nunca outra janela do JARVIS que esteja aberta)
        wins = [w for w in _find_windows() if not user32.IsIconic(w) and _fora_da_tela(w)]
        if wins:
            user32.ShowWindow(wins[0], 0)    # SW_HIDE
            _oculta["hwnd"] = wins[0]
            return True
        time.sleep(0.3)
    return False


def minimize_window(timeout: float = 90.0, manter: float = 25.0) -> None:
    """Minimiza a janela assim que ela aparecer (usado ao iniciar com o Windows).

    No boot o Edge pode demorar a abrir, e depois de carregar ele aplica o "maximizado" de novo; por isso
    esperamos até 90 s pela janela e, por mais alguns segundos, minimizamos de novo se ela voltar.
    """
    import time
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not _find_windows():
        time.sleep(0.5)
    fim = time.monotonic() + manter
    while time.monotonic() < fim:
        for hwnd in _find_windows():
            if not _fora_da_tela(hwnd) and not user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 7)     # SW_SHOWMINNOACTIVE: minimiza sem roubar o foco
        time.sleep(0.4)


def close_browser_windows(profile: Path) -> None:
    """Fecha as janelas do JARVIS (processos do Edge/Chrome com o perfil exclusivo dele)."""
    try:
        import psutil
    except ImportError:
        return
    import re
    # Só esta pasta de perfil (".../navegador" não pode fechar ".../navegador-chrome")
    marca = re.compile(r'--user-data-dir="?' + re.escape(str(profile).lower()) + r'"?(\s|$)')
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or []).lower()
            if marca.search(cmd) and "--type=" not in cmd:
                p.terminate()
        except (psutil.Error, TypeError):
            continue


# --------------------------------------------------------------------------- bloqueio da sessão
class _WTSINFOEX_LEVEL1(ctypes.Structure):
    _fields_ = [("SessionId", ctypes.c_ulong), ("SessionState", ctypes.c_int), ("SessionFlags", ctypes.c_long),
                ("WinStationName", ctypes.c_wchar * 33), ("UserName", ctypes.c_wchar * 21),
                ("DomainName", ctypes.c_wchar * 18), ("LogonTime", ctypes.c_longlong),
                ("ConnectTime", ctypes.c_longlong), ("DisconnectTime", ctypes.c_longlong),
                ("LastInputTime", ctypes.c_longlong), ("CurrentTime", ctypes.c_longlong),
                ("IncomingBytes", ctypes.c_ulong), ("OutgoingBytes", ctypes.c_ulong),
                ("IncomingFrames", ctypes.c_ulong), ("OutgoingFrames", ctypes.c_ulong),
                ("IncomingCompressedBytes", ctypes.c_ulong), ("OutgoingCompressedBytes", ctypes.c_ulong)]


class _WTSINFOEX(ctypes.Structure):
    _fields_ = [("Level", ctypes.c_ulong), ("Data", _WTSINFOEX_LEVEL1)]


def session_locked() -> bool | None:
    """True com a tela bloqueada (Win+L), False desbloqueada, None se não der para saber."""
    if not IS_WINDOWS:
        return None
    wts = ctypes.windll.wtsapi32
    buf, size = ctypes.c_void_p(), ctypes.c_ulong()
    # WTS_CURRENT_SERVER_HANDLE=0, WTS_CURRENT_SESSION=-1, WTSSessionInfoEx=25
    if not wts.WTSQuerySessionInformationW(None, ctypes.c_ulong(0xFFFFFFFF), 25, ctypes.byref(buf), ctypes.byref(size)):
        return None
    try:
        flags = ctypes.cast(buf, ctypes.POINTER(_WTSINFOEX)).contents.Data.SessionFlags
    finally:
        wts.WTSFreeMemory(buf)
    return {0: True, 1: False}.get(flags)        # WTS_SESSIONSTATE_LOCK / UNLOCK


class LockWatcher:
    """Avisa quando a tela é bloqueada e desbloqueada (consulta a cada 2 s, sem janela de mensagens)."""

    def __init__(self, on_lock: Callable[[], None], on_unlock: Callable[[float], None], interval: float = 2.0):
        self.on_lock, self.on_unlock, self.interval = on_lock, on_unlock, interval

    def start(self) -> bool:
        if session_locked() is None:
            return False
        threading.Thread(target=self._run, daemon=True, name="vigia-bloqueio").start()
        return True

    def _run(self) -> None:
        import time
        bloqueada_em: float | None = None
        while True:
            time.sleep(self.interval)
            try:
                locked = session_locked()
                if locked and bloqueada_em is None:
                    bloqueada_em = time.monotonic()
                    self.on_lock()
                elif locked is False and bloqueada_em is not None:
                    duracao, bloqueada_em = time.monotonic() - bloqueada_em, None
                    self.on_unlock(duracao)
            except Exception:
                log.exception("Erro no aviso de bloqueio de tela")


# --------------------------------------------------------------------------- bandeja
class Tray:
    def __init__(self, icon_path: Path, on_open: Callable[[], None], on_quit: Callable[[], None]):
        self.icon_path, self.on_open, self.on_quit = icon_path, on_open, on_quit
        self.icon = None

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image
        except ImportError:
            log.warning("pystray/Pillow indisponível: sem ícone na bandeja")
            return False

        def toggle_autostart(_icon, _item):
            try:
                set_autostart(not autostart_enabled())
            except Exception as exc:
                log.warning("Não consegui mudar o início automático: %s", exc)

        menu = pystray.Menu(
            pystray.MenuItem("Abrir JARVIS", lambda *_: self.on_open(), default=True),
            pystray.MenuItem("Iniciar com o Windows", toggle_autostart, checked=lambda _item: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda *_: self._quit()),
        )
        self.icon = pystray.Icon("JARVIS", Image.open(self.icon_path), "J.A.R.V.I.S. — clique para abrir", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()
        return True

    def notify(self, text: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(text, "J.A.R.V.I.S.")
            except Exception:
                pass

    def _quit(self) -> None:
        if self.icon is not None:
            self.icon.stop()
        self.on_quit()
