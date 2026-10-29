"""Janelinha flutuante do JARVIS: aparece quando a janela principal está minimizada ou só na bandeja.

É uma janela do navegador (modo app, perfil próprio) com a página /flutuante, sem barra de título,
sempre por cima, fora da barra de tarefas e recortada (SetWindowRgn) no formato da barra e do balão (polígonos
com cantos chanfrados a 45°, que o recorte do Windows acompanha sem serrilhar); o resto da janela é "vazado" e
o clique passa para o que estiver atrás. A janela cobre a área de trabalho inteira: a página posiciona as peças
(barra, cards, player, letra) onde você arrastar, e só elas aparecem. Ela não conversa com o servidor:
manda os pedidos para a janela principal (BroadcastChannel), que continua ouvindo, falando e respondendo.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import c_bool, c_int, c_ssize_t, c_uint, c_void_p
from typing import Any, Callable

from .logger import log

IS_WINDOWS = os.name == "nt"
TITULO = "JARVIS Flutuante"

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00C00000, 0x00040000, 0x00080000, 0x00020000, 0x00010000
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
HWND_TOPMOST = c_void_p(-1)
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4

if IS_WINDOWS:
    # Cópias próprias das DLLs, com os tipos declarados (HWND_TOPMOST = -1 e estilos precisam de 64 bits)
    _u = ctypes.WinDLL("user32")
    _g = ctypes.WinDLL("gdi32")
    _u.SetWindowPos.argtypes = [c_void_p, c_void_p, c_int, c_int, c_int, c_int, c_uint]
    _u.GetWindowLongPtrW.argtypes = [c_void_p, c_int]
    _u.GetWindowLongPtrW.restype = c_ssize_t
    _u.SetWindowLongPtrW.argtypes = [c_void_p, c_int, c_ssize_t]
    _u.SetWindowLongPtrW.restype = c_ssize_t
    _u.SetWindowRgn.argtypes = [c_void_p, c_void_p, c_bool]
    for _f in ("ShowWindow",):
        getattr(_u, _f).argtypes = [c_void_p, c_int]
    for _f in ("IsWindow", "IsWindowVisible", "IsIconic"):
        getattr(_u, _f).argtypes = [c_void_p]
    _g.CreateRectRgn.restype = _g.CreatePolygonRgn.restype = c_void_p
    _g.CreatePolygonRgn.argtypes = [c_void_p, c_int, c_int]
    _g.CombineRgn.argtypes = [c_void_p, c_void_p, c_void_p, c_int]
    _g.DeleteObject.argtypes = [c_void_p]


def _dpi_real() -> None:
    """Coordenadas em pixels físicos nesta thread (a janela do Chrome é "per-monitor DPI aware")."""
    try:
        _u.SetThreadDpiAwarenessContext(c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = (ctypes.c_long * 4)()
    _u.GetWindowRect(c_void_p(hwnd), r)
    return r[0], r[1], r[2], r[3]


def _cliente(hwnd: int) -> tuple[int, int, int, int]:
    """Área cliente na tela. O Chrome desenha a própria barra de título dentro dela, em cima da página."""
    r = (ctypes.c_long * 4)()
    _u.GetClientRect(c_void_p(hwnd), r)
    pt = (ctypes.c_long * 2)()
    _u.ClientToScreen(c_void_p(hwnd), pt)
    return pt[0], pt[1], pt[0] + r[2], pt[1] + r[3]


def _titulo(hwnd: int) -> str:
    n = _u.GetWindowTextLengthW(c_void_p(hwnd))
    buf = ctypes.create_unicode_buffer(n + 1)
    _u.GetWindowTextW(c_void_p(hwnd), buf, n + 1)
    return buf.value


def _classe(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(64)
    _u.GetClassNameW(c_void_p(hwnd), buf, 64)
    return buf.value


def _monitores() -> list[tuple[tuple[int, int, int, int], tuple[int, int, int, int], bool]]:
    """(monitor, área de trabalho, é o principal) de cada tela."""
    class INFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.c_long * 4), ("rcWork", ctypes.c_long * 4),
                    ("dwFlags", ctypes.c_ulong)]
    saida: list = []
    Proc = ctypes.WINFUNCTYPE(c_bool, c_void_p, c_void_p, c_void_p, c_void_p)

    def cb(hmon, _hdc, _rect, _lp):
        i = INFO()
        i.cbSize = ctypes.sizeof(INFO)
        if _u.GetMonitorInfoW(c_void_p(hmon), ctypes.byref(i)):
            saida.append((tuple(i.rcMonitor), tuple(i.rcWork), bool(i.dwFlags & 1)))
        return True

    _u.EnumDisplayMonitors(None, None, Proc(cb), 0)
    return saida


def _area_total() -> tuple[int, int, int, int]:
    """Retângulo que cobre a área de trabalho de todos os monitores (para arrastar as peças entre eles).
    Nunca cobre um monitor inteiro (1 px a menos na borda): o Windows não trata a janelinha como "tela cheia"."""
    mons = _monitores()
    if not mons:
        return _area_trabalho()
    x1, y1 = min(w[0] for _, w, _p in mons), min(w[1] for _, w, _p in mons)
    x2, y2 = max(w[2] for _, w, _p in mons), max(w[3] for _, w, _p in mons)
    for m, _w, _p in mons:                       # nenhum monitor inteiro coberto: tira 1 px da borda que encosta nele
        if m[0] >= x1 and m[1] >= y1 and m[2] <= x2 and m[3] <= y2:
            if m[2] == x2:
                x2 -= 1
            elif m[0] == x1:
                x1 += 1
            elif m[3] == y2:
                y2 -= 1
            elif m[1] == y1:
                y1 += 1
    return x1, y1, x2, y2


def _area_trabalho() -> tuple[int, int, int, int]:
    r = (ctypes.c_long * 4)()
    _u.SystemParametersInfoW(0x30, 0, r, 0)    # SPI_GETWORKAREA (tela principal, sem a barra de tarefas)
    return r[0], r[1], r[2], r[3]


class Flutuante:
    """Mostra/esconde a janelinha conforme a janela principal. `abrir` lança a página no navegador."""

    def __init__(self, abrir: Callable[[], None]):
        self._abrir = abrir
        self.hwnd = 0
        self._aberta_em = 0.0
        self._dpr = 1.0
        self._vista = (400.0, 300.0)                    # tamanho da página (px CSS), informado por ela
        self._area = (0, 0, 0, 0)                        # área de trabalho usada na última preparação
        self._regiao: list[list[list[float]]] | None = None   # polígonos da página (px CSS): [[x, y], ...]
        self._preparada = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ localizar
    def _achar(self) -> int:
        if self.hwnd and _u.IsWindow(c_void_p(self.hwnd)):
            return self.hwnd
        achado: list[int] = []
        Enum = ctypes.WINFUNCTYPE(c_bool, c_void_p, c_void_p)

        def cb(hwnd, _):
            t = _titulo(hwnd)
            if (t.startswith(TITULO) or "/flutuante" in t) and _classe(hwnd).startswith("Chrome_WidgetWin"):
                achado.append(hwnd)
                return False
            return True

        _u.EnumWindows(Enum(cb), 0)
        self.hwnd = achado[0] if achado else 0
        self._preparada = 0
        if self.hwnd:
            _u.ShowWindow(self.hwnd, SW_HIDE)          # escondida até a página dizer o formato (sem "piscar")
        return self.hwnd

    def _conteudo(self, hwnd: int) -> tuple[int, int, int, int]:
        """Onde a página está na tela: embaixo da área cliente (a barra do Chrome fica em cima)."""
        x1, _, x2, y2 = _cliente(hwnd)
        return x1, y2 - round(self._vista[1] * self._dpr), x2, y2

    # ------------------------------------------------------------------ preparar (sem moldura, por cima, tela toda)
    def _preparar(self, hwnd: int) -> None:
        estilo = _u.GetWindowLongPtrW(hwnd, GWL_STYLE)
        _u.SetWindowLongPtrW(hwnd, GWL_STYLE, estilo & ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX))
        ex = _u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        _u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)   # fora da barra de tarefas
        _u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        # A página ocupa exatamente a área de trabalho; a barra do Chrome e as bordas ficam de fora (recortadas)
        # (duas passadas: sem a moldura, o Chrome recalcula a área cliente depois do primeiro ajuste)
        ax, ay, ar, ab = self._area = _area_total()
        cli = _cliente(hwnd)
        barra_chrome = max(0, (cli[3] - cli[1]) - round(self._vista[1] * self._dpr))   # o Chrome a desenha dentro da área cliente
        for _ in range(2):
            jan, cli = _rect(hwnd), _cliente(hwnd)
            topo = cli[1] + barra_chrome
            _u.SetWindowPos(hwnd, HWND_TOPMOST, ax - (cli[0] - jan[0]), ay - (topo - jan[1]),
                            (ar - ax) + (jan[2] - jan[0]) - (cli[2] - cli[0]), (ab - ay) + (jan[3] - jan[1]) - (cli[3] - topo),
                            SWP_NOACTIVATE)
            time.sleep(0.05)
        self._preparada = hwnd

    def _aplicar_regiao(self, hwnd: int, redesenhar: bool = True) -> bool:
        if not self._regiao:
            return False
        jan, cont = _rect(hwnd), self._conteudo(hwnd)
        ox, oy, d = cont[0] - jan[0], cont[1] - jan[1], self._dpr
        total = _g.CreateRectRgn(0, 0, 0, 0)
        for forma in self._regiao:
            pts = (ctypes.c_long * (2 * len(forma)))(*[v for x, y in forma for v in (ox + round(x * d), oy + round(y * d))])
            parte = _g.CreatePolygonRgn(ctypes.cast(pts, c_void_p), len(forma), 2)    # WINDING
            _g.CombineRgn(total, total, parte, 2)             # RGN_OR
            _g.DeleteObject(parte)
        # Arrastando, sem redesenhar a janela toda a cada quadro (o navegador já pinta a peça que se move);
        # ao soltar, redesenha uma vez para não sobrar nenhum pedaço velho
        _u.SetWindowRgn(hwnd, total, redesenhar)               # o Windows passa a ser dono da região
        return True

    def telas(self) -> list[dict[str, Any]]:
        """Área de trabalho de cada monitor em px CSS da página (origem = canto da janelinha)."""
        if not IS_WINDOWS:
            return []
        _dpi_real()
        ax, ay = self._area[0], self._area[1]
        d = self._dpr or 1.0
        return [{"x": (w[0] - ax) / d, "y": (w[1] - ay) / d, "w": (w[2] - w[0]) / d, "h": (w[3] - w[1]) / d, "principal": p}
                for _m, w, p in _monitores()]

    # ------------------------------------------------------------------ chamado pela página
    def regiao(self, formas: list[list[list[float]]], dpr: float, vista: tuple[float, float] | None = None,
               redesenhar: bool = True) -> bool:
        if not IS_WINDOWS:
            return False
        _dpi_real()
        with self._lock:
            mudou_dpr = abs((dpr or 1.0) - self._dpr) > 0.01
            self._regiao, self._dpr = formas, dpr or 1.0
            if vista:
                self._vista = vista
            hwnd = self._achar()
            if not hwnd:
                return False
            if mudou_dpr or self._preparada != hwnd:
                self._preparar(hwnd)
            return self._aplicar_regiao(hwnd, redesenhar)

    # ------------------------------------------------------------------ vigia (thread do app.py)
    def atualizar(self, mostrar: bool) -> None:
        if not IS_WINDOWS:
            return
        _dpi_real()
        with self._lock:
            hwnd = self._achar()
            if not mostrar:
                if hwnd and _u.IsWindowVisible(hwnd):
                    _u.ShowWindow(hwnd, SW_HIDE)
                return
            if not hwnd:
                if time.monotonic() - self._aberta_em > 30:     # abre a página (uma vez a cada 30 s, no máximo)
                    self._aberta_em = time.monotonic()
                    self._regiao = None
                    self._abrir()
                return
            if self._preparada != hwnd:
                if not self._regiao:
                    return                                      # a página ainda não disse o formato
                self._preparar(hwnd)
                self._aplicar_regiao(hwnd)
            if _u.GetWindowLongPtrW(hwnd, GWL_STYLE) & WS_CAPTION or _area_total() != self._area:
                self._preparada = 0                             # moldura de volta, ou mudou a resolução/barra de tarefas
                return
            if not _u.IsWindowVisible(hwnd) or _u.IsIconic(hwnd):
                _u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            _u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def vigiar(self, deve_mostrar: Callable[[], bool], parar: threading.Event) -> None:
        while not parar.is_set():
            try:
                self.atualizar(deve_mostrar())
            except Exception:
                log.exception("Janelinha flutuante")
                parar.wait(5)
            parar.wait(0.6)


FLUTUANTE: Flutuante | None = None
