"""Preenche as perguntas de um programa de console (o StatusRobotReport) como se você digitasse.

Roda num processo separado e sem console próprio: "gruda" no console do robô (AttachConsole), lê a
linha onde está o cursor (a pergunta do input()) e, quando reconhece uma pergunta, escreve a resposta
no buffer de entrada do console (WriteConsoleInput) seguida de Enter. Você continua podendo digitar
na janela — perguntas que o JARVIS não sabe responder (ex.: faltas, login) ficam esperando por você.

Uso: python -m jarvis.console_input <pid> '<json [[padrão, resposta], ...]>'
     (no JARVIS.exe: JARVIS.exe --preencher-console <pid> '<json>')
"""
from __future__ import annotations

import ctypes
import json
import re
import sys
import time
from ctypes import wintypes

TIMEOUT = 20 * 60          # segundos: tempo máximo esperando você chegar às perguntas
GENERIC_RW = 0x80000000 | 0x40000000
SHARE_RW = 0x1 | 0x2
OPEN_EXISTING = 3
KEY_EVENT = 0x1
VK_RETURN = 0x0D


class _COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class _SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]


class _CSBI(ctypes.Structure):
    _fields_ = [("dwSize", _COORD), ("dwCursorPosition", _COORD), ("wAttributes", wintypes.WORD),
                ("srWindow", _SMALL_RECT), ("dwMaximumWindowSize", _COORD)]


class _KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("UnicodeChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class _INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]


def proxima_resposta(linha: str, regras: list[tuple[str, str]], ja: set[str]) -> tuple[str, str] | None:
    """Qual regra responde a pergunta desta linha (cada pergunta só uma vez). Função pura, testável."""
    texto = linha.rstrip()
    for padrao, resposta in regras:
        if padrao not in ja and re.search(padrao, texto, re.I):
            return padrao, resposta
    return None


class _Console:
    def __init__(self, pid: int):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.FreeConsole()
        if not self.k32.AttachConsole(wintypes.DWORD(pid)):
            raise OSError(f"AttachConsole({pid}) falhou: {ctypes.get_last_error()}")
        self.k32.CreateFileW.restype = wintypes.HANDLE
        self.out = self.k32.CreateFileW("CONOUT$", GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)
        self.inp = self.k32.CreateFileW("CONIN$", GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)

    def linha_do_cursor(self) -> str:
        info = _CSBI()
        if not self.k32.GetConsoleScreenBufferInfo(wintypes.HANDLE(self.out), ctypes.byref(info)):
            return ""
        x, y = info.dwCursorPosition.X, info.dwCursorPosition.Y
        buf = ctypes.create_unicode_buffer(max(x, 1) + 1)
        lidos = wintypes.DWORD()
        self.k32.ReadConsoleOutputCharacterW(wintypes.HANDLE(self.out), buf, x, _COORD(0, y), ctypes.byref(lidos))
        return buf.value[: lidos.value]

    def digitar(self, texto: str) -> None:
        teclas = [(ch, 0) for ch in texto] + [("\r", VK_RETURN)]
        recs = (_INPUT_RECORD * (len(teclas) * 2))()
        for i, (ch, vk) in enumerate(teclas):
            for j, down in enumerate((True, False)):
                r = recs[i * 2 + j]
                r.EventType = KEY_EVENT
                r.Event.KeyEvent.bKeyDown = down
                r.Event.KeyEvent.wRepeatCount = 1
                r.Event.KeyEvent.wVirtualKeyCode = vk
                r.Event.KeyEvent.UnicodeChar = ch
        escritos = wintypes.DWORD()
        self.k32.WriteConsoleInputW(wintypes.HANDLE(self.inp), recs, len(recs), ctypes.byref(escritos))


def _vivo(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return True


def preencher(pid: int, regras: list[tuple[str, str]]) -> int:
    con = _Console(pid)
    ja: set[str] = set()
    fim = time.monotonic() + TIMEOUT
    while time.monotonic() < fim and len(ja) < len(regras) and _vivo(pid):
        time.sleep(0.4)
        achado = proxima_resposta(con.linha_do_cursor(), regras, ja)
        if achado:
            time.sleep(0.3)                  # dá tempo do input() começar a ler
            con.digitar(achado[1])
            ja.add(achado[0])
    return 0


def main(argv: list[str]) -> int:
    pid, regras = int(argv[0]), [tuple(r) for r in json.loads(argv[1])]
    try:
        return preencher(pid, regras)        # type: ignore[arg-type]
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
